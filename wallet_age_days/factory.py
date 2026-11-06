"""One production implementation shared by HTTP, Python and CLI entry points."""

from pathlib import Path
import threading

from eth_wallet_check.http import JSONHTTP
from .config import settings
from .providers import AppearanceSources
from .service import WalletAgeService
from .store import EpochStore


DEFAULT_DB = str(Path(__file__).resolve().parent.parent / "data" / "wallet-age-days.sqlite3")


def build_service(path=None, *, env_file=None, environ=None, http=None, store=None, executor=None):
    config = settings(env_file, environ)
    endpoints = config["endpoints"]
    if http is None:
        http = JSONHTTP(timeout=2.6, headers_by_url=config["headers_by_url"])
    elif hasattr(http, "_headers_by_url"):
        http._headers_by_url.update(config["headers_by_url"])
    alchemy = next((url for name, url in endpoints if name == "alchemy"), None)
    sources = AppearanceSources(http, endpoints, alchemy_endpoint=alchemy,
        quicknode_history_enabled=config["quicknode_history_enabled"],
        etherscan_key=config["etherscan_key"])
    return WalletAgeService(sources, store if store is not None else EpochStore(
        path or config["cache_path"] or DEFAULT_DB), http, executor=executor)


_default = None
_lock = threading.Lock()


def default_service():
    global _default
    with _lock:
        if _default is None:
            _default = build_service()
        return _default
