#!/usr/bin/env python3
"""Reproduce the fixed 100-case benchmark corpus without network calls.

Generated cases are 160-bit address strings, not created/funded wallets. No
private keys are generated or stored. Expected absence for those cases is a
synthetic expectation, not independently proven historical ground truth.
"""

import hashlib
import json
from pathlib import Path
import re


SEED = "eth-wallet-benchmark-2026-11-05:"
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
CONTRACTS = [
    ("WETH9", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "https://developers.uniswap.org/deployments"),
    ("USDC", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "https://developers.circle.com/stablecoins/usdc-contract-addresses"),
    ("USDT", "0xdac17f958d2ee523a2206206994597c13d831ec7", "https://tether.to/en/supported-protocols/"),
    ("DAI", "0x6b175474e89094c44da98b954eedeac495271d0f", "https://etherscan.io/address/0x6b175474e89094c44da98b954eedeac495271d0f"),
    ("UNI", "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", "https://developers.uniswap.org/deployments"),
    ("LINK", "0x514910771af9ca656af840dff83e8264ecf986ca", "https://docs.chain.link/resources/link-token-contracts"),
    ("WBTC", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", "https://etherscan.io/address/0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"),
    ("AAVE", "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9", "https://etherscan.io/address/0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9"),
    ("MKR", "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2", "https://etherscan.io/address/0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2"),
    ("COMP", "0xc00e94cb662c3520282e6f5717214004a7f26888", "https://etherscan.io/address/0xc00e94cb662c3520282e6f5717214004a7f26888"),
    ("Uniswap V2 Factory", "0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f", "https://developers.uniswap.org/docs/protocols/v2/deployments"),
    ("Uniswap V2 Router02", "0x7a250d5630b4cf539739df2c5dacb4c659f2488d", "https://developers.uniswap.org/docs/protocols/v2/deployments"),
    ("Uniswap V3 Factory", "0x1f98431c8ad98523631ae4a59f267346ea31f984", "https://developers.uniswap.org/deployments"),
    ("Uniswap V3 SwapRouter", "0xe592427a0aece92de3edee1f18e0157c05861564", "https://developers.uniswap.org/deployments"),
    ("Uniswap SwapRouter02", "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45", "https://developers.uniswap.org/deployments"),
    ("Uniswap V3 NonfungiblePositionManager", "0xc36442b4a4522e871399cd717abdd847ab11fe88", "https://developers.uniswap.org/deployments"),
    ("Permit2", "0x000000000022d473030f116ddee9f6b43ac78ba3", "https://developers.uniswap.org/deployments"),
    ("ENS Registry", "0x00000000000c2e074ec69a0dfb2997ba6c7d2e1e", "https://docs.ens.domains/learn/deployments/"),
    ("ENS BaseRegistrarImplementation", "0x57f1887a8bf19b14fc0df6fd9b2acc9af147ea85", "https://docs.ens.domains/learn/deployments/"),
    ("Beacon deposit contract", "0x00000000219ab540356cbb839cbe05303d7705fa", "https://ethereum.org/staking/deposit-contract/"),
]

# Senders included in a canonical block read from a public execution RPC on
# 2026-11-05. These hashes establish previous use, not the first-ever use.
OBSERVED_SENDERS = [
    ("0xc917c3fa468f2c4b9c84c72caa46420eb9825249", "0xe875086da1da9171d30ed01e00c228d200c68a3dbc351fc067dd3bd15bbe0b09"),
    ("0x37c728c153eeb40ee907cb5b41b342d896878846", "0x8bce43f48849b91e0699d25aad8c939724e20d79ac20a21dabfa6a7a86f8e347"),
    ("0x35f7cff299f48b1b2a542224fd823152e437c654", "0x45e15b7d58eb8438a67a736a7b204eadb7d4168ba3b548b336486f373eb5c07e"),
    ("0xccba2faab763603b2894a5f203bd335dc26d16b8", "0x63a75812a9a15574067c98ed2ce570d5193e1d7579d909788bc0806263d2e7e2"),
    ("0xbdc8f547b06f06d38a102217ca28ce7ed9340805", "0xcc8a40d088eb13e6ca1e3ec06dfe0b6d01301fd1724c0dab817fb6be3142246c"),
    ("0xb5e5ba15a5aa58df03d91549496d8cd67a15f858", "0x6f7fb7f428ce69072e91d1f3e255e6502f228a3c8bf045dd0341d66910694073"),
    ("0xd82c5660cac4169de051d7df34323f99c21176a7", "0x15f961f36a38754aead92c3dac2365caefda53b07a0224bf7019965173ba5dbc"),
    ("0x4817a075bb59c1ed2d21e8240ff1561750175515", "0x590f5b7ee1c3df54b80340a06ae27fecac5fe197af3f706306d8d1466cd5e1df"),
    ("0x403e2b67f70ee3d10ec88c9b3d6de9eb1c7200d8", "0xc18ff9f840ce40fe1b8d2f0b4be3a9016706df7173dca1d5be2d9773d8b88ece"),
    ("0x91e33655188280afd5042a1fba723a731a117f1b", "0x89b787f9deafc470b01215226171da4cf669d16a3052777defa52d03ede4fadf"),
    ("0xc066ac5d385419b1a8c43a0e146fa439837a8b8c", "0x824a19bf71648af9a0aed507e09c0b23a9a99e138819b89716f2ddae3c7de6c0"),
    ("0x6d14296c706813c642045bc6102ee6e2fcaf8528", "0xf8b42b9608330a88b1b1b44ea771622d12e919d4f00c4d759c73401e9c4638e1"),
    ("0x51649cf810ea9f1f61165044b7149070c67f79c8", "0x43bfca5f83794c2cce9d3140890b41996dda8b0aadc03ef8925bf0a1f03a3f67"),
    ("0x07fcdbe8283f1b67cee5d5a4151cec8e8184301f", "0xa0680246ae7098cacb18162a71a1bd21a1e05e8134fd3ce125849523f59f4101"),
    ("0xb42f812a44c22cc6b861478900401ee759ebead6", "0x367da3cbbc76b933c7132f4d05955a1d9aba05143554ed7a951f6e19f5a93810"),
    ("0x7a0e37f1543d0adb4e7ba7a27ea16b926b35a67c", "0xf27a6f83075374616d906ebe3073afcbf01be2208cf6034cfc08fcd74dfa7a11"),
    ("0xaa8ba7d4611437141192e7ceced531bc0a133efb", "0xe8b91a82d9cc730e82b0b2f47cef924433f2fb23a739bf4740013c34d9c5df3e"),
    ("0xd37ac934502619cee14908cef22138f4b98e06b4", "0xaf365350131819637d482836bb10c12b2c05221f73f429864421bbd9db00cc2f"),
]


def build_cases():
    cases = []

    def add(category, label, address, valid, appeared, level, references, **extra):
        cases.append({
            "id": f"eth-{len(cases)+1:03d}", "category": category,
            "label": label, "address": address, "expected_valid_format": valid,
            "expected_has_appeared": appeared,
            "expected_new_address": None if appeared is None else not appeared,
            "ground_truth_level": level, "reference_urls": references, **extra,
        })

    for label, address in [
        ("Vitalik-associated public EOA", "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"),
        ("Ethereum Foundation public EOA", "0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae"),
    ]:
        add("existing_eoa", label, address, True, True, "known_onchain",
            [f"https://etherscan.io/address/{address}"],
            expected_age_note="No first-use timestamp asserted by this corpus; age controls are separate.")
    for index, (address, tx_hash) in enumerate(OBSERVED_SENDERS, 1):
        add("existing_eoa", f"Observed block 26350416 sender {index:02d}", address,
            True, True, "known_onchain", [f"https://etherscan.io/tx/{tx_hash}",
            "https://etherscan.io/block/26350416"],
            proof={"type": "canonical_transaction_sender", "transaction_hash": tx_hash,
                   "block_number": 26350416, "block_timestamp": 1793903195,
                   "note": "Inclusion proves prior use, not that this was first activity."})
    code_sizes = [3124,2186,11075,7904,12567,3153,4582,2491,3379,6795,
                  13859,21943,24535,12070,24497,24384,9152,5346,10542,6358]
    for (label, address, reference), code_size in zip(CONTRACTS, code_sizes):
        add("existing_contract", label, address, True, True, "known_onchain",
            list(dict.fromkeys([reference, f"https://etherscan.io/address/{address}"])),
            proof={"type": "deployed_code", "block_number": 26350422,
                   "code_bytes": code_size,
                   "note": "Nonempty code was verified via eth_getCode at the stated block; deployment age is verified separately."})
    for index in range(40):
        address = "0x" + hashlib.sha256((SEED + str(index)).encode()).digest()[-20:].hex()
        add("generated_valid", f"Generated unobserved candidate {index+1:02d}", address,
            True, False, "synthetic_unobserved_candidate",
            ["https://ethereum.org/en/developers/docs/accounts/"],
            proof={"type": "deterministic_synthetic_address", "seed": SEED + str(index),
                   "note": "No key was generated. Expected absence is a synthetic test expectation, not proof of never having appeared."})

    valid = "0x" + "a" * 40
    malformed = [
        ("Empty input", ""), ("Prefix only", "0x"), ("Far too short", "0x1234"),
        ("39 hex digits", "0x" + "a" * 39), ("41 hex digits", "0x" + "a" * 41),
        ("Missing prefix", "a" * 40), ("Uppercase X prefix", "0X" + "a" * 40),
        ("All nonhex digits", "0x" + "g" * 40), ("Single nonhex suffix", "0x" + "a" * 39 + "z"),
        ("Leading space", " " + valid), ("Trailing space", valid + " "),
        ("Trailing newline", valid + "\n"), ("Leading tab", "\t" + valid),
        ("Full-width Unicode digits", "0x" + "Ａ" * 40),
        ("Emoji suffix", "0x" + "a" * 39 + "😀"),
        ("Cyrillic x prefix", "0х" + "a" * 40), ("ENS name", "vitalik.eth"),
        ("Explorer URL instead of address", "https://etherscan.io/address/" + valid),
        ("Embedded underscore", "0x" + "a" * 20 + "_" + "a" * 19),
        ("Embedded NUL", "0x" + "a" * 39 + "\x00"),
    ]
    for label, address in malformed:
        add("malformed", label, address, False, None, "syntax",
            ["https://eips.ethereum.org/EIPS/eip-55"])
    assert len(cases) == len({row["address"] for row in cases}) == 100
    assert len({row["address"].lower() for row in cases if row["expected_valid_format"]}) == 80
    assert all(bool(ADDRESS.fullmatch(row["address"])) == row["expected_valid_format"] for row in cases)
    return cases


def main():
    document = {"metadata": {
        "schema_version": 1, "network": "ethereum-mainnet", "chain_id": 1,
        "dataset_date": "2026-11-05", "case_count": 100,
        "category_counts": {"existing_eoa": 20, "existing_contract": 20,
                            "generated_valid": 40, "malformed": 20},
        "generated_address_method": "Last 20 bytes of SHA-256(seed); no keys generated or saved.",
        "classification_limits": "Synthetic absence is an expectation, not absolute historical proof. Existing controls have positive on-chain evidence. Wallet key creation time is not recorded on chain.",
        "selection_limits": "18 EOAs are convenience samples from one observed block, not a random sample of all Ethereum users; canonical contracts are deliberately popular controls.",
    }, "cases": build_cases()}
    destination = Path(__file__).with_name("addresses.json")
    destination.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(document['cases'])} cases to {destination}")


if __name__ == "__main__":
    main()
