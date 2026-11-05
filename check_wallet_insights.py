#!/usr/bin/env python3
"""Inspect one wallet without starting the HTTP service."""

import argparse
import json

from wallet_age_days.service import WalletAgeError
from wallet_insights.factory import build_service


def main(argv=None):
    parser = argparse.ArgumentParser(description="WalletInsights: balances, nonce, code and sourced labels")
    parser.add_argument("blockchain")
    parser.add_argument("address")
    parser.add_argument("--env-file")
    parser.add_argument("--cache-db")
    args = parser.parse_args(argv)
    service = None
    try:
        service = build_service(args.cache_db, env_file=args.env_file)
        status, body = service.check(args.blockchain, args.address)
        print(json.dumps(body, indent=2))
        return 0 if status == 200 else 3
    except WalletAgeError as exc:
        print(json.dumps(exc.message))
        return 2 if exc.status == 400 else 4
    except Exception:
        print(json.dumps("unknown"))
        return 3
    finally:
        if service is not None:
            service.close()


if __name__ == "__main__":
    raise SystemExit(main())
