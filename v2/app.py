"""aiohttp entry point; version routing and legacy compatibility live here."""

import argparse
import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
from functools import lru_cache
import hmac
import ipaddress
import json
from math import ceil
import os
from pathlib import Path
import re
from time import monotonic
import tomllib
from urllib.parse import quote, urlsplit

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

from .cache import Cache
from .feeds import Feeds
from .service import AgeError, Service


ROOT = Path(__file__).resolve().parent
SERVICE = web.AppKey("service", object)
VERSIONS = ("v1", "v2")
ROUTES = {"health", "docs", "openapi.json", "walletAgeDays", "walletInsights"}
DEMO_KEY = "demo_wallet_age_test_key_replace_me"


def _client_keys(env, legacy):
    clients = {"legacy": legacy} if legacy else {}
    for name, key in env.items():
        if not name.startswith("WALLET_CLIENT_KEY_"):
            continue
        client = name.removeprefix("WALLET_CLIENT_KEY_").lower()
        if (not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", client)
                or client in {"anonymous", "unauthenticated", "legacy"} or client in clients
                or not key.strip() or key.strip() != key or key in clients.values()):
            raise ValueError("Client keys must have unique IDs and unique nonempty values")
        clients[client] = key
    return clients


def _access_log(request, event, railway, secrets):
    # Log only validated fields. The raw path, headers and body may contain secrets.
    ip = request.remote
    event["ipSource"] = "peer"
    if railway:
        raw = request.headers.get("X-Real-IP", "")
        try:
            if "%" not in raw:
                ip = str(ipaddress.ip_address(raw))
                event["ipSource"] = "railway"
        except ValueError:
            pass
        request_id = request.headers.get("X-Railway-Request-Id", "")
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id):
            event["requestId"] = request_id
    event["clientIp"] = ip
    for field in ("clientId", "wallet", "blockchain", "clientIp", "requestId"):
        value = event.get(field)
        if value and any(key and key.casefold() in value.casefold() for key in secrets):
            event[field] = None
    try:
        print(json.dumps(event, separators=(",", ":")), flush=True)
    except (OSError, ValueError):
        pass  # An unavailable log stream must not change the HTTP response.


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
    env = config.get("_env", {})
    clients = _client_keys(env, config.get("server", {}).get("api_key", ""))
    secret_names = {"ANCHAIN_DATA_API_KEY", "ANCHAIN_API_KEY"}
    for feed in config.get("feeds", {}).values():
        secret_names.update(feed.get(field) for field in ("key_env", "token_env"))
    log_secrets = (*clients.values(), *(env[name] for name in secret_names if env.get(name)))
    railway = bool(env.get("RAILWAY_ENVIRONMENT_ID"))
    require_key = railway or bool(clients)
    credentials = [(name, key.encode()) for name, key in clients.items()
                   if not railway or key != DEMO_KEY]
    quota = config.get("server", {}).get("requests_per_minute", 60)
    if type(quota) is not int or not 1 <= quota <= 100_000:
        raise ValueError("Invalid requests_per_minute")
    recent = defaultdict(deque)

    def retry_after(bucket):
        # Only configured identities and two shared buckets can allocate state.
        hits, now = recent[bucket], monotonic()
        while hits and hits[0] <= now - 60:
            hits.popleft()
        if len(hits) >= quota:
            return max(1, ceil(60 - (now - hits[0])))
        hits.append(now)
        return 0

    app = web.Application(client_max_size=1024, handler_args={
        "auto_decompress": False, "max_headers": 64, "lingering_time": 0,
    })
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
    old = dict(legacy or {})
    if "WalletInsights" in old:
        old["walletInsights"] = old.pop("WalletInsights")
    legacy_lock, legacy_active = asyncio.Lock(), 0
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

    docs_page = (ROOT / "docs.html").read_text(encoding="utf-8")

    @lru_cache(maxsize=4)
    def spec(version, pinned, client_view=False):
        doc = json.loads((ROOT.parent / "openapi.json").read_text())
        doc["paths"]["/docs"] = {"get": {"operationId": "apiReference", "summary": "Read the API reference",
            "security": [], "responses": {"200": {"description": "Browser-readable OpenAPI documentation.",
            "content": {"text/html": {"schema": {"type": "string"}}}}}}}
        doc["components"]["securitySchemes"]["HeaderApiKeyAuth"]["description"] = (
            "Send your assigned client key in the x-api-key header." if require_key else
            "This local service accepts requests without a key.")
        if require_key:
            doc["security"] = [{"HeaderApiKeyAuth": []}]
        for path, operations in doc["paths"].items():
            if path in {"/walletAgeDays", "/walletInsights", "/openapi.json", "/docs"}:
                for method in ("get", "post"):
                    if method in operations:
                        operations[method]["responses"]["429"] = {
                            "description": "Request limit reached. Retry after the Retry-After interval in seconds.",
                            "headers": {"Retry-After": {"schema": {"type": "integer"}}},
                            "content": {"application/json": {"schema": {"const": "rate limited"}}},
                        }
        doc["servers"] = [{"url": f"/{version}" if pinned else "/"}]
        if client_view:
            doc["paths"] = {path: doc["paths"][path] for path in ("/walletAgeDays", "/walletInsights")}
            doc["info"].update(
                version="latest",
                description=(
                    doc["info"]["description"] + "\n\n"
                    + (ROOT.parent / "docs" / "CLIENT_QUICKSTART.md").read_text(encoding="utf-8") + "\n\n"
                    + (ROOT / "withdrawal-screening.md").read_text(encoding="utf-8")
                ),
            )
            return json.dumps(doc, separators=(",", ":")).encode()
        if pinned:
            if version == "v2":
                doc["info"]["title"] = "WalletAgeDays API"
                doc["info"]["version"] = "2.0.0"
                doc["info"]["description"] = "Age-only screening API. Verified observed activity; Ethereum coverage first."
                doc["paths"].pop("/walletInsights", None)
            return json.dumps(doc, separators=(",", ":")).encode()
        original = doc["paths"].copy()
        doc["info"]["version"] = "2.0.0"
        doc["info"]["description"] = "Latest compatible API by default; /v1 pins legacy, /v2 pins age V2."
        for prefix in ("v1", "v2", "latest"):
            for route, definition in original.items():
                if prefix != "v2" or route != "/walletInsights":
                    copy = json.loads(json.dumps(definition))
                    for operation in copy.values():
                        if isinstance(operation, dict) and "operationId" in operation:
                            operation["operationId"] += "_" + prefix
                    doc["paths"][f"/{prefix}{route}"] = copy
        doc["x-latest-age-version"] = latest
        return json.dumps(doc, separators=(",", ":")).encode()

    async def handle(request):
        parts = request.path.removeprefix("/").split("/")
        pinned = parts[0] if len(parts) == 2 and parts[0] != "latest" else None
        version, route = (latest, parts[0]) if len(parts) == 1 else (parts[0], parts[-1])
        if route == "WalletInsights":
            route = "walletInsights"
        version = latest if version == "latest" else version
        known = len(parts) <= 2 and version in VERSIONS
        supplied = request.headers.getall("x-api-key", [])
        client = None
        if len(supplied) == 1:
            token = supplied[0].encode("utf-8", errors="surrogateescape")
            client = next((name for name, key in credentials if hmac.compare_digest(key, token)), None)
        event = {
            "message": "wallet.access", "level": "info",
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "clientId": client or "anonymous",
            "method": request.method if request.method in {"GET", "POST", "HEAD", "PUT", "PATCH", "OPTIONS", "DELETE"} else "OTHER",
            "endpoint": request.path if known and route in ROUTES else "unknown",
            "blockchain": None, "wallet": None,
        }
        insights = route == "walletInsights"
        if insights and known and pinned != "v2":
            version = "v1"  # Latest available Insights remains the legacy API.
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                   "X-API-Version": version if known else latest}
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
            data_route = route in {"walletAgeDays", "walletInsights"}
            if data_route and require_key and not client:
                event["clientId"] = "unauthenticated"
            if data_route or (route in {"openapi.json", "docs"} and request.method == "GET"):
                wait = retry_after(client or ("@data" if data_route else "@docs"))
                if wait:
                    headers["Retry-After"] = str(wait)
                    raise AgeError(429, "rate limited")
            if route in {"health", "openapi.json", "docs"} and request.method == "GET":
                if route == "docs":
                    headers["Content-Security-Policy"] = (
                        "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                        "connect-src 'self'; img-src 'self' data:; font-src 'self' data:; "
                        "base-uri 'none'; frame-ancestors 'none'"
                    )
                    return web.Response(text=docs_page, content_type="text/html", headers=headers)
                if route == "openapi.json":
                    client_view = request.path == "/openapi.json" and request.query.get("view") == "latest"
                    return web.Response(body=spec(version, pinned, client_view),
                                        content_type="application/json", headers=headers)
                body = "ok"
            else:
                if route not in {"walletAgeDays", "walletInsights"}:
                    raise AgeError(404, "not_found")
                if require_key and not client:
                    raise AgeError(401, "unauthorized")
                try:
                    if request.headers.getall("Content-Encoding", []) not in ([], ["identity"]):
                        raise ValueError("Unsupported content encoding")
                    if request.method == "GET":
                        if request.can_read_body or len(request.rel_url.raw_query_string) > 1024 or len(request.query) != 2:
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
                address, chain = values["address"], values["blockchain"]
                if isinstance(address, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
                    event["wallet"] = address.lower()
                if isinstance(chain, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", chain):
                    event["blockchain"] = chain.lower()
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
        except asyncio.CancelledError:
            status = 499
            raise
        except AgeError as exc:
            status, body = exc.status, exc.message
            if exc.coverage:
                headers["X-Wallet-Age-Coverage"] = exc.coverage
        except Exception as exc:
            if type(exc).__module__ == "wallet_age_days.service" and hasattr(exc, "status"):
                status, body = exc.status, exc.message
            else:
                status, body = 503, "unknown"
        finally:
            event["status"] = status
            _access_log(request, event, railway, (*log_secrets, *supplied))
        return web.json_response(body, status=status, headers=headers)

    async def cleanup(_app):
        await service.close()
        await asyncio.gather(*legacy_tasks, return_exceptions=True)
        for checker in old.values():
            await asyncio.to_thread(checker.close)
        if session:
            await session.close()

    app.router.add_static("/docs-assets/", ROOT / "static")
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
