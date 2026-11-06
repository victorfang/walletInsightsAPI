"""Explicit config-file opt-in; no keys or private URLs in returned metadata."""

import os
from pathlib import Path

from eth_wallet_check.config import _https_url, rpc_endpoints


CONFIG_KEYS = frozenset({"ALCHEMY_RPC_URL", "ALCHEMY_API_KEY", "QUICKNODE_RPC_URL",
    "QUICKNODE_ETH_URL", "QUICKNODE_ETH_AUTH_TOKEN", "ANCHAIN_DATA_API_BASE_URL",
    "ANCHAIN_DATA_API_KEY", "ANCHAIN_API_KEY", "WALLET_INSIGHTS_DB",
    "ETHERSCAN_API_KEY", "WALLET_AGE_DB", "WALLET_AGE_QUICKNODE_HISTORY"})

ASSETS = (
    {"symbol": "USDC", "address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "decimals": 6},
    {"symbol": "USDT", "address": "0xdac17f958d2ee523a2206206994597c13d831ec7", "decimals": 6},
    {"symbol": "DAI", "address": "0x6b175474e89094c44da98b954eedeac495271d0f", "decimals": 18},
)


def provider_values(env_file=None, environ=None):
    """Read only documented settings; no dotenv interpolation or key logging."""
    values = {}
    if env_file:
        for line in Path(env_file).expanduser().read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.removeprefix("export ").split("=", 1)
            if key.strip() in CONFIG_KEYS:
                values[key.strip()] = value.strip().strip("\"'")
    env = os.environ if environ is None else environ
    values.update({key: env[key].strip() for key in CONFIG_KEYS if key in env})
    # Example URLs are often left beside real keys in a copied .env.example.
    # Ignore only unmistakable placeholders, preserving genuine explicit URLs.
    for key in ("ALCHEMY_RPC_URL", "QUICKNODE_RPC_URL", "QUICKNODE_ETH_URL"):
        value = values.get(key, "")
        if any(marker in value.lower() for marker in
               ("<", ">", "your_alchemy", "your_quicknode", "your_api", "your_endpoint",
                "your_token", "your_key", "your-ethereum-endpoint", "replace-me", "replace_me")):
            values.pop(key, None)
    if not values.get("QUICKNODE_RPC_URL") and values.get("QUICKNODE_ETH_URL"):
        values["QUICKNODE_RPC_URL"] = values["QUICKNODE_ETH_URL"]
    return values


def settings(env_file=None, environ=None):
    values = provider_values(env_file, environ)
    endpoints = rpc_endpoints(values)
    headers = {}
    if values.get("QUICKNODE_ETH_AUTH_TOKEN"):
        for name, endpoint in endpoints:
            if name == "quicknode":
                headers[endpoint] = {"x-token": values["QUICKNODE_ETH_AUTH_TOKEN"]}
    return {"endpoints": endpoints, "headers_by_url": headers,
            "anchain_base": _https_url(values.get("ANCHAIN_DATA_API_BASE_URL") or
                                      "https://api.anchainai.com", "ANCHAIN_DATA_API_BASE_URL").rstrip("/"),
            "anchain_key": values.get("ANCHAIN_DATA_API_KEY") or values.get("ANCHAIN_API_KEY") or "",
            "cache_path": values.get("WALLET_INSIGHTS_DB")}
