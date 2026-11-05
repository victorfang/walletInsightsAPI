"""Command line and JSON integer output for one walletAgeDays request."""

import argparse
import json
import os

from eth_wallet_check.http import JSONHTTP
from .factory import DEFAULT_DB, build_service
from .service import WalletAgeError, validate


def main(argv=None):
    parser = argparse.ArgumentParser(description="walletAgeDays: blockchain address -> integer completed days")
    parser.add_argument("blockchain")
    parser.add_argument("address")
    parser.add_argument("--cache-db", default=os.environ.get("WALLET_AGE_DB") or DEFAULT_DB)
    parser.add_argument("--timeout", type=float, default=2.6, help="Upstream timeout, capped by the shared 2.6s request budget")
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 2.6:
        parser.error("--timeout must be greater than zero and at most 2.6 seconds")
    service = None
    try:
        validate(args.blockchain, args.address)
        service = build_service(args.cache_db, http=JSONHTTP(timeout=args.timeout))
        print(json.dumps(service.walletAgeDays(args.blockchain, args.address)))
        return 0
    except WalletAgeError as exc:
        print(json.dumps(exc.message))
        return {400: 2, 404: 4, 501: 5, 503: 3}.get(exc.status, 3)
    except Exception:
        print(json.dumps("unknown"))
        return 3
    finally:
        if service is not None:
            service.close()
