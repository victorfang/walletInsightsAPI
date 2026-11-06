"""aiohttp entry point; version routing and legacy compatibility live here."""

import argparse
import asyncio
import hmac
import json
import os
from pathlib import Path
import tomllib
from urllib.parse import quote, urlsplit

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

from .cache import Cache
from .feeds import Feeds
from .service import AgeError, Service


ROOT = Path(__file__).resolve().parent
SERVICE = web.AppKey("service", object)
VERSIONS = ("v1", "v2")


def load_config(path=ROOT / "config.toml", env_file=None, environ=None):
    path = Path(path).resolve()
    config, values = tomllib.loads(path.read_text()), {}
    if env_file:
        for line in Path(env_file).read_text().splitlines():
            line = line.strip().removeprefix("export ")
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    shell = os.environ if environ is None else environ
    values.update(shell)
    for feed in config["feeds"].values():
        key = values.get(feed.get("key_env"), "")
        env_names = feed.get("url_env", [])
        env_names = [env_names] if isinstance(env_names, str) else env_names
        env_names = sorted(env_names, key=lambda name: name not in shell)
        explicit = next((values[name] for name in env_names if values.get(name) and
                         not any(marker in values[name].lower() for marker in
                                 ("your_", "your-", "replace_me", "replace-me", "<", ">"))), "")
        url = explicit or (feed.get("url", "").replace("{key}", quote(key, safe=""))
                           if key or "{key}" not in feed.get("url", "") else "")
        if not feed.get("enabled", True) or (feed.get("required_key") and not key):
            url = ""
        if url:
            try:
                parsed = urlsplit(url)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError()
                parsed.port
            except ValueError:
                raise ValueError("Provider URLs must use HTTPS without userinfo") from None
        feed["url"] = url
        if key and feed.get("key_param"):
            feed.setdefault("params", {})[feed["key_param"]] = key
        token = values.get(feed.get("token_env"), "")
        if token:
            feed.setdefault("headers", {})[feed.get("token_header", "x-token")] = token
        if feed["adapter"] not in {"alchemy", "etherscan_compatible", "evm_rpc"}:
            raise ValueError("Unknown provider adapter")
    config["chains"] = {name: row for name, row in config["chains"].items() if row.get("enabled", True)}
    for chain in config["chains"].values():
        if chain.get("family") != "evm" or not chain.get("chain_id"):
            raise ValueError("Only EVM chain adapters are implemented")
        if any(name not in config["feeds"] for name in chain["history"] + chain["rpc"]):
            raise ValueError("Chain references an unknown feed")
    config["wallet_age"]["database"] = str((path.parent / config["wallet_age"]["database"]).resolve())
    config.setdefault("server", {})["api_key"] = values.get("WALLET_CHECK_API_KEY", "")
    config["_env"] = values
    return config


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate input")
        result[key] = value
    return result


async def create_app(config=None, *, service=None, legacy=None):
    config = load_config() if config is None else config
    selected = config.get("api", {}).get("latest", "latest")
    latest = VERSIONS[-1] if selected == "latest" else selected
    if latest not in VERSIONS:
        raise ValueError("Unknown latest API version")
    app = web.Application(client_max_size=1024)
    session = None
    if service is None:
        session = ClientSession(timeout=ClientTimeout(total=2.6), connector=TCPConnector(limit=64))
        try:
            service = Service(Feeds(session, config), Cache(config["wallet_age"]["database"],
                              config["wallet_age"].get("max_entries", 1_000_000)), config)
        except BaseException:
            await session.close()
            raise
    app[SERVICE] = service
    old, legacy_lock, legacy_active = ({} if legacy is None else legacy), asyncio.Lock(), 0
    legacy_tasks = set()

    async def legacy_check(route, values):
        nonlocal legacy_active
        if not config.get("legacy", {}).get("enabled", True):
            raise AgeError(503, "unknown")
        if legacy_active >= config.get("legacy", {}).get("max_inflight", 4):
            raise AgeError(503, "unknown")
        legacy_active += 1
        async def run():
            try:
                async with legacy_lock:
                    if route not in old:
                        # Only this compatibility edge imports the frozen V1 implementation.
                        from wallet_age_days.factory import build_service as age_factory
                        from wallet_insights.factory import build_service as insights_factory
                        factory = age_factory if route == "walletAgeDays" else insights_factory
                        old[route] = await asyncio.to_thread(factory, environ=config.get("_env", {}))
                return await asyncio.to_thread(old[route].check, values["blockchain"], values["address"])
            finally:
                nonlocal legacy_active
                legacy_active -= 1
        task = asyncio.create_task(run())
        legacy_tasks.add(task)
        def finished(done):
            legacy_tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        return await asyncio.shield(task)

    def spec(version, pinned):
        doc = json.loads((ROOT.parent / "openapi.json").read_text())
        doc["servers"] = [{"url": f"/{version}" if pinned else "/"}]
        if pinned:
            if version == "v2":
                doc["info"]["title"] = "WalletAgeDays API"
                doc["info"]["version"] = "2.0.0"
                doc["info"]["description"] = "Age-only screening API. Verified observed activity; Ethereum coverage first."
                doc["paths"].pop("/WalletInsights", None)
            return doc
        original = doc["paths"].copy()
        doc["info"]["version"] = "2.0.0"
        doc["info"]["description"] = "Latest compatible API by default; /v1 pins legacy, /v2 pins age V2."
        for prefix in ("v1", "v2", "latest"):
            for route, definition in original.items():
                if prefix != "v2" or route != "/WalletInsights":
                    copy = json.loads(json.dumps(definition))
                    for operation in copy.values():
                        if isinstance(operation, dict) and "operationId" in operation:
                            operation["operationId"] += "_" + prefix
                    doc["paths"][f"/{prefix}{route}"] = copy
        doc["x-latest-age-version"] = latest
        return doc

    async def handle(request):
        parts = request.path.removeprefix("/").split("/")
        pinned = parts[0] if len(parts) == 2 and parts[0] != "latest" else None
        version, route = (latest, parts[0]) if len(parts) == 1 else (parts[0], parts[-1])
        version = latest if version == "latest" else version
        known = len(parts) <= 2 and version in VERSIONS
        insights = route == "WalletInsights"
        if insights and known and pinned != "v2":
            version = "v1"  # Latest available Insights remains the legacy API.
        headers = {"Cache-Control": "no-store", "X-API-Version": version if known else latest}
        if not insights:
            headers.update({"X-Wallet-Age-Coverage": "ethereum" if version == "v1" else service.coverage,
                            "X-Wallet-Age-Basis": "first-observed-activity", "X-Wallet-Age-Quality": "lower-bound"})
        else:
            headers["X-Wallet-Insights-Coverage"] = "ethereum"
        status, body = 200, None
        try:
            if request.method not in {"GET", "POST"}:
                raise AgeError(405, "method not allowed")
            if not known:
                raise AgeError(404, "not_found")
            if route in {"health", "openapi.json"} and request.method == "GET":
                body = "ok" if route == "health" else spec(version, pinned)
            else:
                if route not in {"walletAgeDays", "WalletInsights"}:
                    raise AgeError(404, "not_found")
                expected = config.get("server", {}).get("api_key", "")
                if expected and not hmac.compare_digest(expected.encode(), request.headers.get("x-api-key", "").encode()):
                    raise AgeError(401, "unauthorized")
                try:
                    if request.method == "GET":
                        if len(request.query) != 2:
                            raise ValueError("Invalid input count")
                        values = dict(request.query)
                    else:
                        if (request.query_string or request.content_type != "application/json" or
                                not 0 < (request.content_length or 0) <= 1024):
                            raise ValueError("Invalid body")
                        values = json.loads(await asyncio.wait_for(request.read(), 0.2), object_pairs_hook=_unique)
                    if not isinstance(values, dict) or set(values) != {"blockchain", "address"}:
                        raise ValueError("Invalid fields")
                except (ValueError, UnicodeError, asyncio.TimeoutError, web.HTTPRequestEntityTooLarge):
                    raise AgeError(400, "invalid request")
                if insights and version == "v2":
                    raise AgeError(501, "coming soon contact info@anchain.ai")
                result = (await legacy_check(route, values) if version == "v1" else
                          await service.check(values["blockchain"], values["address"]))
                if insights:
                    status, body = result
                    if isinstance(body, dict) and "cache" in body:
                        hits = [row["hit"] for row in body["cache"].values()]
                        headers["X-Wallet-Insights-Cache"] = "hit" if all(hits) else "mixed" if any(hits) else "miss"
                else:
                    body = result.days
                    headers.update({"X-Wallet-Age-Coverage": getattr(result, "coverage", headers["X-Wallet-Age-Coverage"]),
                        "X-Wallet-Age-Cache": result.cache, "X-Wallet-Age-Finalized": str(result.finalized).lower(),
                        "X-Wallet-Age-Evidence": result.evidence, "X-Wallet-Age-Sources": str(result.positive_sources)})
        except AgeError as exc:
            status, body = exc.status, exc.message
            if exc.coverage:
                headers["X-Wallet-Age-Coverage"] = exc.coverage
        except Exception as exc:
            if type(exc).__module__ == "wallet_age_days.service" and hasattr(exc, "status"):
                status, body = exc.status, exc.message
            else:
                status, body = 503, "unknown"
        return web.json_response(body, status=status, headers=headers)

    async def cleanup(_app):
        await service.close()
        await asyncio.gather(*legacy_tasks, return_exceptions=True)
        for checker in old.values():
            await asyncio.to_thread(checker.close)
        if session:
            await session.close()

    app.router.add_route("*", "/{path:.*}", handle)
    app.on_cleanup.append(cleanup)
    return app


def main():
    parser = argparse.ArgumentParser(description="walletAgeDays: latest by default, explicit /v1 and /v2")
    parser.add_argument("--config", default=str(ROOT / "config.toml"))
    parser.add_argument("--env-file")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    try:
        config = load_config(args.config, args.env_file)
    except (OSError, ValueError):
        parser.error("Invalid configuration; check TOML, environment names and file paths")
    web.run_app(create_app(config), host=args.host or config["server"].get("host", "127.0.0.1"),
                port=args.port or config["server"].get("port", 8787), access_log=None, handler_cancellation=True)


if __name__ == "__main__":
    main()
