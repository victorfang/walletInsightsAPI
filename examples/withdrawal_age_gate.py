#!/usr/bin/env python3
"""Example client policy for walletAgeDays; never executes/approves a withdrawal.

Usage:
    python3 examples/withdrawal_age_gate.py ETH 0xADDRESS --new-beneficiary

The seven-day threshold is illustrative, not a validated fraud classifier.
Beneficiary/account-risk flags must come from the client's trusted backend.
"""

import argparse
from dataclasses import asdict, dataclass
from http.client import HTTPException
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ETHEREUM = {"eth", "ethereum", "eth-mainnet"}


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    age_days: int | None = None


def evaluate(status, body, *, coverage, finalized, blockchain="ETH",
             new_beneficiary=False, account_risk=False, recent_days=7):
    """Map API evidence to triage. There is deliberately no 'approve' action."""
    if type(recent_days) is not int or recent_days < 1:
        raise ValueError("recent_days must be a positive integer")
    if status == 400 and body == "invalid":
        return Decision("reject", "invalid_destination")
    age = body if status == 200 and type(body) is int and body >= 0 else None
    if account_risk:
        return Decision("pending_review", "account_risk", age)
    # This client example screens Ethereum withdrawals only. An ETH-only
    # family result must not satisfy a Polygon-specific check. A future family
    # aggregate also cannot establish first use on one particular chain.
    if not isinstance(blockchain, str) or blockchain.lower() not in ETHEREUM or coverage != "ethereum":
        return Decision("pending_review", "withdrawal_chain_not_covered")
    if status == 404 and body == "not_found":
        return Decision("step_up", "no_observed_history")
    if status != 200:
        return Decision("pending_review", "age_check_unavailable")
    if age is None or finalized != "true":
        return Decision("pending_review", "invalid_or_unverified_age_response")
    # Zero is a valid result. Never test age using `if not age`.
    if age < recent_days:
        return Decision("step_up", "recent_observed_activity", age)
    if new_beneficiary:
        return Decision("step_up", "new_beneficiary_for_customer", age)
    return Decision("continue_other_checks", "older_observed_activity", age)


def screen_destination(blockchain, address, *, new_beneficiary=False,
                       account_risk=False, recent_days=7,
                       base_url="http://127.0.0.1:8787", api_key=None):
    """Call the API once with a 3s socket timeout; preserve unresolved outcomes.

    The timeout covers socket operations, not arbitrary DNS/OS stalls. A
    production withdrawal orchestrator should also enforce its overall deadline.
    base_url is trusted backend configuration, not end-user input.
    """
    if type(recent_days) is not int or recent_days < 1:
        raise ValueError("recent_days must be a positive integer")
    if not isinstance(blockchain, str) or blockchain.lower() not in ETHEREUM:
        return Decision("pending_review", "withdrawal_chain_not_covered")
    url = base_url.rstrip("/") + "/walletAgeDays?" + urlencode({
        "blockchain": blockchain, "address": address})
    headers = {"Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    try:
        try:
            response = urlopen(Request(url, headers=headers), timeout=3.0)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read(4097)
            if len(raw) > 4096:
                return Decision("pending_review", "invalid_api_response")
            body = json.loads(raw)
            return evaluate(response.status, body,
                coverage=response.headers.get("X-Wallet-Age-Coverage", ""),
                finalized=response.headers.get("X-Wallet-Age-Finalized", ""),
                blockchain=blockchain, new_beneficiary=new_beneficiary,
                account_risk=account_risk, recent_days=recent_days)
    except (HTTPException, OSError, ValueError):
        # No API error, timeout, malformed response or authentication failure
        # becomes a fresh age of zero or an authorization to withdraw.
        return Decision("pending_review", "age_check_unavailable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("blockchain")
    parser.add_argument("address")
    parser.add_argument("--new-beneficiary", action="store_true")
    parser.add_argument("--account-risk", action="store_true")
    parser.add_argument("--recent-days", type=int, default=7)
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    args = parser.parse_args()
    if args.recent_days < 1:
        parser.error("--recent-days must be positive")
    result = screen_destination(args.blockchain, args.address,
        new_beneficiary=args.new_beneficiary, account_risk=args.account_risk,
        recent_days=args.recent_days, base_url=args.base_url,
        api_key=os.environ.get("WALLET_CHECK_API_KEY"))
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()
