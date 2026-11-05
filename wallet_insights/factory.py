"""Explicit WalletInsights config, independently cached from walletAgeDays."""

from pathlib import Path

from eth_wallet_check.http import JSONHTTP
from .anchain import AnChainLabels
from .cache import InsightsCache
from .config import ASSETS, settings
from .rpc import SnapshotRPC
from .service import WalletInsightsService


DEFAULT_DB = str(Path(__file__).resolve().parent.parent / "data" / "wallet-insights.sqlite3")


def build_service(cache_path=None, *, env_file=None, environ=None, http=None, cache=None, executor=None):
    config = settings(env_file, environ)
    if http is None:
        http = JSONHTTP(timeout=2.6, headers_by_url=config["headers_by_url"])
    else:
        http._headers_by_url.update(config["headers_by_url"])
    cache = cache if cache is not None else InsightsCache(cache_path or config["cache_path"] or DEFAULT_DB)
    return WalletInsightsService(SnapshotRPC(http, config["endpoints"]),
        AnChainLabels(http, config["anchain_base"], config["anchain_key"]),
        cache, http, ASSETS, executor=executor)
