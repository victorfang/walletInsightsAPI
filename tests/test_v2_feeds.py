"""Local upstream fixtures: no credentials, vendor quota or external network."""

import asyncio
import json
import unittest

from aiohttp import ClientSession, web

from v2.feeds import FeedError, Feeds


ADDRESS = "0x" + "a" * 40
OTHER = "0x" + "b" * 40
TX = "0x" + "1" * 64
BLOCK_HASH = "0x" + "2" * 64
EPOCH = 1_700_000_000


class FeedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mode, self.requests = "valid", []
        self.release = asyncio.Event()
        app = web.Application()
        app.router.add_route("*", "/{source}", self.upstream)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        self.session = ClientSession()
        self.config = {"feeds": {
            "index": {"adapter": "etherscan_compatible", "url": self.base + "/index", "group": "vendor"},
            "alchemy": {"adapter": "alchemy", "url": self.base + "/alchemy"},
            "rpc": {"adapter": "evm_rpc", "url": self.base + "/rpc"},
            "bad_rpc": {"adapter": "evm_rpc", "url": self.base + "/bad_rpc"},
        }}
        self.chain = {"chain_id": 1, "history": ["index"], "rpc": ["rpc"]}
        self.feeds = Feeds(self.session, self.config)

    async def asyncTearDown(self):
        self.release.set()
        await self.session.close()
        await self.runner.cleanup()

    async def upstream(self, request):
        source = request.match_info["source"]
        self.requests.append((source, dict(request.query)))
        if source == "index":
            if self.mode == "redirect":
                raise web.HTTPFound(self.base + "/secret_sink")
            if self.mode == "empty":
                return web.json_response({"status": "0", "message": "No transactions found", "result": []})
            if request.query["action"] == "getcontractcreation":
                if self.mode == "hung_creation":
                    await self.release.wait()
                if self.mode == "partial":
                    return web.json_response({"status": "0", "message": "NOTOK", "result": "rate limited"})
                rows = ([{"contractAddress": ADDRESS, "txHash": TX}]
                        if self.mode in ("creation", "failed_creation") else [])
            else:
                rows = [{"from": OTHER if self.mode == "unrelated" else ADDRESS, "to": OTHER,
                         "hash": TX, "blockNumber": "7", "timeStamp": str(EPOCH + (self.mode == "wrong_time"))}]
                if self.mode in ("creation", "failed_creation"):
                    rows = []
            data = {"status": "1", "message": "OK", "result": rows}
            if self.mode == "chunked":
                response = web.StreamResponse(headers={"Content-Type": "application/json"})
                await response.prepare(request)
                encoded = json.dumps(data).encode()
                await response.write(encoded[:10])
                await asyncio.sleep(0.01)
                await response.write(encoded[10:])
                await response.write_eof()
                return response
            return web.json_response(data)
        if source == "secret_sink":
            self.fail("Credentialed redirect was followed")
        payload = await request.json()
        values = []
        for call in payload:
            method = call["method"]
            if method == "alchemy_getAssetTransfers":
                query = call["params"][0]
                rows = ([{"from": ADDRESS, "to": OTHER, "hash": TX, "blockNum": "0x7",
                          "metadata": {"blockTimestamp": "2023-11-14T22:13:20Z"}, "category": "external"}]
                        if "fromAddress" in query else [])
                result = {"transfers": rows}
            elif method == "eth_chainId":
                result = "0x2" if source == "bad_rpc" else "0x1"
            elif method == "eth_getTransactionReceipt":
                result = {"transactionHash": TX, "blockHash": BLOCK_HASH, "blockNumber": "0x7",
                          "from": ADDRESS, "to": OTHER, "status": "0x1", "logs": []}
                if self.mode in ("creation", "failed_creation"):
                    result.update(fromAddress=OTHER, to=None, contractAddress=ADDRESS,
                                  status="0x0" if self.mode == "failed_creation" else "0x1")
            elif call["params"][0] == "finalized":
                result = {"number": "0x6" if self.mode == "unfinalized" else "0xa", "hash": BLOCK_HASH}
            else:
                result = {"number": "0x7", "hash": BLOCK_HASH, "timestamp": hex(EPOCH),
                          "transactions": [] if self.mode == "missing_tx" else [TX]}
            values.append({"jsonrpc": "2.0", "id": call["id"], "result": result})
        if self.mode == "duplicate_ids":
            values = values[:1] * len(values)
        return web.json_response(values)

    async def lookup(self):
        jobs = self.feeds.jobs(self.chain, ADDRESS)
        self.assertEqual(jobs[0][0], "vendor")
        return await jobs[0][1]

    async def test_indexed_epoch_has_canonical_finalized_proof(self):
        result = await self.lookup()
        self.assertEqual((result.epoch, result.complete), (EPOCH, True))
        self.assertEqual(sum(source == "rpc" for source, _ in self.requests), 2)

    async def test_alchemy_group_requires_both_directions(self):
        self.chain["history"] = ["alchemy"]
        result = await self.feeds.jobs(self.chain, ADDRESS)[0][1]
        self.assertEqual((result.epoch, result.complete), (EPOCH, True))

    async def test_partial_group_retains_verified_minimum_without_vote(self):
        self.mode = "partial"
        result = await self.lookup()
        self.assertEqual((result.epoch, result.complete), (EPOCH, False))

    async def test_hung_sibling_retains_partial_before_application_deadline(self):
        self.mode = "hung_creation"
        self.config["wallet_age"] = {"deadline_ms": 150, "provider_deadline_ms": 90}
        started = asyncio.get_running_loop().time()
        result = await self.lookup()
        self.assertEqual((result.epoch, result.complete), (EPOCH, False))
        self.assertLess(asyncio.get_running_loop().time() - started, 0.15)

    async def test_source_cutoff_cancels_all_owned_proof_tasks(self):
        self.config["wallet_age"] = {"deadline_ms": 150, "provider_deadline_ms": 90}
        self.chain["rpc"] = ["rpc", "bad_rpc"]
        running, cleaned = set(), []

        async def hanging_proof(name, *_):
            running.add(name)
            try:
                await asyncio.Event().wait()
            finally:
                running.remove(name)
                cleaned.append(name)

        self.feeds._proof_rpc = hanging_proof
        result = await self.lookup()
        self.assertEqual((result.epoch, result.complete), (None, False))
        self.assertEqual(running, set())
        self.assertCountEqual(cleaned, ["rpc", "bad_rpc"])

    async def test_empty_scope_is_not_an_epoch(self):
        self.mode = "empty"
        result = await self.lookup()
        self.assertIsNone(result.epoch)
        self.assertTrue(result.complete)
        self.assertFalse(any(source == "rpc" for source, _ in self.requests))

    async def test_wrong_chain_rpc_falls_back(self):
        self.chain["rpc"] = ["bad_rpc", "rpc"]
        self.assertEqual((await self.lookup()).epoch, EPOCH)

    async def test_memoized_proof_is_shared_between_history_providers(self):
        self.chain["history"] = ["alchemy", "index"]
        results = await asyncio.gather(*(job for _, job in self.feeds.jobs(self.chain, ADDRESS)))
        self.assertEqual([result.epoch for result in results], [EPOCH, EPOCH])
        self.assertEqual(sum(source == "rpc" for source, _ in self.requests), 2)

    async def test_contract_creation_is_verified_separately(self):
        self.mode = "creation"
        result = await self.lookup()
        self.assertEqual((result.epoch, result.complete), (EPOCH, True))
        self.mode = "failed_creation"
        result = await self.lookup()
        self.assertEqual((result.epoch, result.complete), (None, False))

    async def test_indexed_timestamp_must_match_canonical_block(self):
        self.mode = "wrong_time"
        result = await self.lookup()
        self.assertEqual((result.epoch, result.complete), (None, False))

    async def test_unfinalized_or_unrelated_or_missing_transaction_is_unknown(self):
        for mode in ("unfinalized", "unrelated", "missing_tx", "duplicate_ids"):
            with self.subTest(mode=mode):
                self.mode = mode
                result = await self.lookup()
                self.assertIsNone(result.epoch)
                self.assertFalse(result.complete)

    async def test_redirect_is_not_followed(self):
        self.mode = "redirect"
        result = await self.lookup()
        self.assertFalse(result.complete)
        self.assertFalse(any(source == "secret_sink" for source, _ in self.requests))

    async def test_chunked_json_read_is_complete(self):
        self.mode = "chunked"
        self.assertEqual((await self.lookup()).epoch, EPOCH)

    async def test_rpc_errors_are_sanitized(self):
        self.mode = "duplicate_ids"
        with self.assertRaisesRegex(FeedError, "Invalid RPC result"):
            await self.feeds._rpc("rpc", [("eth_chainId", []), ("eth_chainId", [])])
