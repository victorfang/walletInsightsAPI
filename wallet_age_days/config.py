"""Explicit shared provider configuration for the integer age endpoint."""

from eth_wallet_check.config import rpc_endpoints
from wallet_insights.config import provider_values


def settings(env_file=None, environ=None):
    values = provider_values(env_file, environ)
    endpoints = rpc_endpoints(values)
    headers = {}
    if values.get("QUICKNODE_ETH_AUTH_TOKEN"):
        for name, endpoint in endpoints:
            if name == "quicknode":
                headers[endpoint] = {"x-token": values["QUICKNODE_ETH_AUTH_TOKEN"]}
    return {"endpoints": endpoints, "headers_by_url": headers,
            "etherscan_key": values.get("ETHERSCAN_API_KEY") or "",
            "quicknode_history_enabled": values.get("WALLET_AGE_QUICKNODE_HISTORY") == "1",
            "cache_path": values.get("WALLET_AGE_DB")}
