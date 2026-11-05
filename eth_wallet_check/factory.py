"""Executable chain adapters shared by the command line and HTTP service."""

from .config import rpc_endpoints
import os

from .age_store import AgeStore
from .fast_history import FastHistory
from .fast_service import FastWalletChecker
from .rpc import PublicRPC


def build_ethereum_checker(http, cache, age_store=None):
    """Use configured paid endpoints and public fallbacks for Ethereum mainnet."""
    endpoints = rpc_endpoints()
    age_path = os.environ.get("AGE_CACHE_DB") or (
        ":memory:" if cache.path == ":memory:" else cache.path + ".age.sqlite3")
    return FastWalletChecker(FastHistory(http, endpoints), PublicRPC(http, endpoints),
                             cache, age_store if age_store is not None else AgeStore(age_path), http)


# Only implemented protocols belong here. Static planned-chain metadata never
# grants a protocol access to the Ethereum validator, providers, or cache.
CHECKER_FACTORIES = {"eth": build_ethereum_checker}
