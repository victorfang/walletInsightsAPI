"""Run the walletAgeDays HTTP service using only Python's standard library."""

import argparse
import os

from .factory import build_service
from .server import WalletAgeHTTPServer
from wallet_insights.factory import build_service as build_insights


def main():
    parser = argparse.ArgumentParser(description="walletAgeDays: blockchain + address -> integer days")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8787, type=int)
    parser.add_argument("--cache-db")
    parser.add_argument("--insights-cache-db", default=os.environ.get("WALLET_INSIGHTS_DB"))
    parser.add_argument("--env-file", help="Explicit provider config file for both endpoints; shell settings take precedence")
    args = parser.parse_args()
    try:
        service = build_service(args.cache_db, env_file=args.env_file)
        insights = build_insights(args.insights_cache_db, env_file=args.env_file)
    except (ValueError, OSError) as exc:
        # Configuration errors identify the setting, never credential values.
        parser.error(str(exc) if isinstance(exc, ValueError) else "Unable to open cache database")
    try:
        with WalletAgeHTTPServer((args.host, args.port), service,
                                 os.environ.get("WALLET_CHECK_API_KEY") or None, insights=insights) as server:
            print(f"walletAgeDays and walletInsights listening on http://{args.host}:{server.server_port}; coverage={service.coverage}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        service.close()
        insights.close()


if __name__ == "__main__":
    main()
