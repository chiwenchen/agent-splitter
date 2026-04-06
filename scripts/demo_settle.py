#!/usr/bin/env python3
"""
SplitSettle Phase A Demo — End-to-end settlement execution on Base Sepolia.

This script:
1. Creates a wallet group via POST /v1/groups
2. Calls POST /v1/split_settle with group_id to get ABI-encoded calldata
3. Submits the calldata to Base Sepolia to execute the USDC transfer
4. Prints the Basescan URL for verification

Required env vars:
  SPLIT_SETTLE_API_KEY  — API key for the SplitSettle service
  DEMO_PRIVATE_KEY      — Private key for a funded Base Sepolia wallet (the "from" wallet)

Optional env vars:
  SPLIT_SETTLE_API_URL  — Override API base URL (default: production)

Install dependencies:
  pip3 install requests web3

Usage:
  SPLIT_SETTLE_API_KEY=xxx DEMO_PRIVATE_KEY=0x... python3 scripts/demo_settle.py
"""

import json
import os
import sys

try:
    import requests
    from web3 import Web3
except ImportError:
    print("Missing dependencies. Install with: pip3 install requests web3")
    sys.exit(1)

# Configuration
API_BASE = os.environ.get(
    "SPLIT_SETTLE_API_URL",
    "https://split.redarch.dev",
)
API_KEY = os.environ.get("SPLIT_SETTLE_API_KEY", "")
PRIVATE_KEY = os.environ.get("DEMO_PRIVATE_KEY", "")
BASE_SEPOLIA_RPC = "https://sepolia.base.org"
BASESCAN_URL = "https://sepolia.basescan.org/tx"

if not API_KEY:
    print("Error: SPLIT_SETTLE_API_KEY env var is required")
    sys.exit(1)
if not PRIVATE_KEY:
    print("Error: DEMO_PRIVATE_KEY env var is required")
    sys.exit(1)


def main():
    w3 = Web3(Web3.HTTPProvider(BASE_SEPOLIA_RPC))
    account = w3.eth.account.from_key(PRIVATE_KEY)
    print(f"Demo wallet: {account.address}")
    print(f"Balance: {w3.eth.get_balance(account.address)} wei")
    print()

    headers = {"x-api-key": API_KEY, "Content-Type": "application/json"}

    # Step 1: Create a group with two wallets
    # The "from" wallet is the demo wallet, "to" is a burn address for demo purposes
    to_address = "0x000000000000000000000000000000000000dEaD"
    group_body = {
        "group_id": f"demo-{int(__import__('time').time())}",
        "participants": [
            {"name": "Payer", "wallet_address": account.address},
            {"name": "Receiver", "wallet_address": Web3.to_checksum_address(to_address)},
        ],
    }

    print(f"1. Creating group '{group_body['group_id']}'...")
    resp = requests.post(f"{API_BASE}/v1/groups", json=group_body, headers=headers)
    if resp.status_code != 200:
        print(f"   Failed: {resp.status_code} {resp.text}")
        sys.exit(1)
    print(f"   OK: {resp.json()}")
    print()

    # Step 2: Call split_settle with group_id
    settle_body = {
        "currency": "USD",
        "group_id": group_body["group_id"],
        "participants": ["Payer", "Receiver"],
        "expenses": [
            {
                "description": "Demo expense",
                "paid_by": "Receiver",
                "amount": 0.01,
                "split_among": ["Payer", "Receiver"],
            }
        ],
    }

    print("2. Calling split_settle with group_id...")
    resp = requests.post(f"{API_BASE}/v1/split_settle", json=settle_body, headers=headers)
    if resp.status_code != 200:
        print(f"   Failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    result = resp.json()
    print(f"   Settlements: {result['settlements']}")
    print(f"   Execution block: {json.dumps(result.get('execution', {}), indent=2)}")
    print()

    if "execution" not in result:
        print("   No execution block (no settlements needed)")
        return

    # Step 3: Submit calldata to Base Sepolia
    transfers = result["execution"]["transfers"]
    token_contract = result["execution"]["token_contract"]

    for i, transfer in enumerate(transfers):
        print(f"3. Submitting transfer {i + 1}/{len(transfers)}...")
        print(f"   From: {transfer['from_wallet']}")
        print(f"   To:   {transfer['to_wallet']}")
        print(f"   Amount: {transfer['amount_wei']} wei ({int(transfer['amount_wei']) / 1_000_000} USDC)")

        # Build transaction
        nonce = w3.eth.get_transaction_count(account.address)
        tx = {
            "to": Web3.to_checksum_address(token_contract),
            "data": transfer["calldata"],
            "gas": 100_000,
            "gasPrice": w3.eth.gas_price,
            "nonce": nonce,
            "chainId": 84532,  # Base Sepolia chain ID
        }

        # Sign and send
        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"   TX hash: {tx_hash.hex()}")
        print(f"   Basescan: {BASESCAN_URL}/{tx_hash.hex()}")
        print()

        # Wait for receipt
        print("   Waiting for confirmation...")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        status = "SUCCESS" if receipt["status"] == 1 else "FAILED"
        print(f"   {status} (block {receipt['blockNumber']}, gas used {receipt['gasUsed']})")
        print()

    print("Done! Check the Basescan URLs above to verify the transfers.")


if __name__ == "__main__":
    main()
