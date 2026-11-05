"""One production implementation shared by HTTP, Python and CLI entry points."""

import os
from pathlib import Path
import threading

from eth_wallet_check.config import rpc_endpoints
from eth_wallet_check.http import JSONHTTP
from .providers import AppearanceSources
from .service import WalletAgeService
from .store import EpochStore


DEFAULT_DB = str(Path(__file__).resolve().parent.parent / "data" / "wallet-age-days.sqlite3")


def build_service(path=None, *, http=None, store=None, executor=None):
    endpoints = rpc_endpoints()
    http = http if http is not None else JSONHTTP(timeout=2.6)
    alchemy = next((url for name, url in endpoints if name == "alchemy"), None)
    sources = AppearanceSources(http, endpoints, alchemy_endpoint=alchemy,
        quicknode_history_enabled=os.environ.get("WALLET_AGE_QUICKNODE_HISTORY") == "1")
    return WalletAgeService(sources, store if store is not None else EpochStore(
        path or os.environ.get("WALLET_AGE_DB") or DEFAULT_DB), http, executor=executor)


_default = None
_lock = threading.Lock()


def default_service():
    global _default
    with _lock:
        if _default is None:
            _default = build_service()
        return _default
