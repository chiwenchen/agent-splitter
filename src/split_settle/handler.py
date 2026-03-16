import json
import logging
import os
import time
import urllib.request

logger = logging.getLogger(__name__)

# x402 payment constants
PAYMENT_RECIPIENT      = "0xD87C7aED8809BB2d50A7ABE69e286a2242bC3e68"
PAYMENT_TOKEN_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC on Base
PAYMENT_NETWORK        = "base-mainnet"
PAYMENT_AMOUNT_DISPLAY = "0.001"
PAYMENT_AMOUNT_MIN     = 1000  # 0.001 USDC at 6 decimals
TRANSFER_EVENT_SIG     = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

_cached_api_key    = None
_cached_alchemy_url = None


def _get_api_key() -> str:
    global _cached_api_key
    if _cached_api_key is not None:
        return _cached_api_key
    direct_key = os.environ.get("API_KEY", "")
    if direct_key:
        _cached_api_key = direct_key
        return _cached_api_key
    secret_arn = os.environ.get("SECRET_ARN", "")
    if secret_arn:
        import boto3
        client = boto3.client("secretsmanager", region_name="ap-northeast-1")
        response = client.get_secret_value(SecretId=secret_arn)
        _cached_api_key = response.get("SecretString", "")
    else:
        _cached_api_key = ""
    return _cached_api_key


def _get_alchemy_url() -> str:
    global _cached_alchemy_url
    if _cached_alchemy_url is not None:
        return _cached_alchemy_url
    direct_url = os.environ.get("ALCHEMY_RPC_URL", "")
    if direct_url:
        _cached_alchemy_url = direct_url
        return _cached_alchemy_url
    secret_arn = os.environ.get("ALCHEMY_SECRET_ARN", "")
    if secret_arn:
        import boto3
        client = boto3.client("secretsmanager", region_name="ap-northeast-1")
        response = client.get_secret_value(SecretId=secret_arn)
        _cached_alchemy_url = response.get("SecretString", "")
    else:
        _cached_alchemy_url = ""
    return _cached_alchemy_url


def _rpc_call(method: str, params: list) -> dict:
    """Make a JSON-RPC call to Alchemy."""
    url = _get_alchemy_url()
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read())


def _is_tx_used(tx_hash: str) -> bool:
    import boto3
    table = os.environ.get("PAYMENTS_TABLE", "")
    if not table:
        return False
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    response = client.get_item(TableName=table, Key={"tx_hash": {"S": tx_hash}})
    return "Item" in response


def _mark_tx_used(tx_hash: str) -> None:
    import boto3
    table = os.environ.get("PAYMENTS_TABLE", "")
    if not table:
        return
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    ttl = int(time.time()) + 86400 * 365  # 1 year TTL
    try:
        client.put_item(
            TableName=table,
            Item={"tx_hash": {"S": tx_hash}, "ttl_expiry": {"N": str(ttl)}},
            ConditionExpression="attribute_not_exists(tx_hash)",
        )
    except client.exceptions.ConditionalCheckFailedException:
        raise ValueError("tx already used (race condition)")


def _verify_payment(tx_hash: str, network: str) -> tuple:
    """Verify an on-chain USDC payment. Returns (is_valid, error_message)."""
    tx_hash = tx_hash.lower()

    if network != PAYMENT_NETWORK:
        return False, f"wrong network: expected {PAYMENT_NETWORK}, got {network}"

    if _is_tx_used(tx_hash):
        return False, "tx already used"

    # Fetch receipt from Alchemy
    try:
        result = _rpc_call("eth_getTransactionReceipt", [tx_hash])
    except Exception as e:
        logger.exception("Alchemy RPC error")
        return False, "payment verification unavailable"

    receipt = result.get("result")
    if receipt is None:
        return False, "transaction not found or not yet mined"

    if receipt.get("status") != "0x1":
        return False, "transaction reverted"

    # Check at least 1 confirmation
    try:
        block_result = _rpc_call("eth_blockNumber", [])
        current_block = int(block_result["result"], 16)
        tx_block = int(receipt["blockNumber"], 16)
        if current_block - tx_block < 1:
            return False, "transaction not yet confirmed"
    except Exception:
        logger.exception("block number check failed")
        return False, "payment verification unavailable"

    # Parse ERC-20 Transfer logs
    recipient_padded = "000000000000000000000000" + PAYMENT_RECIPIENT[2:].lower()
    for log in receipt.get("logs", []):
        topics = log.get("topics", [])
        if len(topics) < 3:
            continue
        if topics[0].lower() != TRANSFER_EVENT_SIG:
            continue
        if log.get("address", "").lower() != PAYMENT_TOKEN_CONTRACT.lower():
            continue
        if topics[2].lower() != "0x" + recipient_padded:
            continue
        amount = int(log.get("data", "0x0"), 16)
        if amount < PAYMENT_AMOUNT_MIN:
            return False, f"amount too low: got {amount}, need {PAYMENT_AMOUNT_MIN}"
        # Valid payment found — mark as used
        try:
            _mark_tx_used(tx_hash)
        except ValueError as e:
            return False, str(e)
        return True, ""

    return False, "no valid USDC transfer found in transaction logs"


def _payment_required_response(reason: str = "") -> dict:
    body = {
        "error": "Payment Required",
        "x402": {
            "amount": PAYMENT_AMOUNT_DISPLAY,
            "currency": "USDC",
            "network": PAYMENT_NETWORK,
            "recipient": PAYMENT_RECIPIENT,
            "token_contract": PAYMENT_TOKEN_CONTRACT,
            "instructions": (
                f"Send >= {PAYMENT_AMOUNT_DISPLAY} USDC on {PAYMENT_NETWORK} to the recipient address, "
                'then retry with header: X-PAYMENT: {"tx_hash": "0x...", "network": "base-mainnet"}'
            ),
        },
    }
    if reason:
        body["reason"] = reason
    return {"statusCode": 402, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}


OPENAPI_SCHEMA = {
    "openapi": "3.1.0",
    "info": {
        "title": "SplitSettle API",
        "description": "Calculate the minimum number of transfers to settle shared expenses.",
        "version": "1.0.0",
    },
    "servers": [
        {
            "url": "https://aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com",
            "description": "Production",
        }
    ],
    "paths": {
        "/split_settle": {
            "post": {
                "summary": "Calculate optimal settlement plan",
                "description": "Given a list of participants and shared expenses, returns the minimum transfers needed to settle all debts.",
                "security": [{"ApiKeyAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/SplitSettleRequest"},
                            "example": {
                                "currency": "TWD",
                                "participants": ["Alice", "Bob", "Carol"],
                                "expenses": [
                                    {
                                        "description": "Dinner",
                                        "paid_by": "Alice",
                                        "amount": 1200,
                                        "split_among": ["Alice", "Bob", "Carol"],
                                    },
                                    {
                                        "description": "Taxi",
                                        "paid_by": "Bob",
                                        "amount": 300,
                                        "split_among": ["Alice", "Bob", "Carol"],
                                    },
                                ],
                            },
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Settlement plan",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SplitSettleResponse"}
                            }
                        },
                    },
                    "400": {"description": "Invalid request"},
                    "403": {"description": "Invalid or missing API key"},
                },
            }
        }
    },
    "components": {
        "securitySchemes": {
            "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "x-api-key"}
        },
        "schemas": {
            "Expense": {
                "type": "object",
                "required": ["paid_by", "amount", "split_among"],
                "properties": {
                    "description": {"type": "string"},
                    "paid_by": {"type": "string"},
                    "amount": {"type": "number", "exclusiveMinimum": 0},
                    "split_among": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
            },
            "SplitSettleRequest": {
                "type": "object",
                "required": ["currency", "participants", "expenses"],
                "properties": {
                    "currency": {"type": "string", "description": "ISO 4217 currency code"},
                    "participants": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                        "maxItems": 20,
                    },
                    "expenses": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Expense"},
                        "minItems": 1,
                    },
                },
            },
            "ParticipantSummary": {
                "type": "object",
                "properties": {
                    "participant": {"type": "string"},
                    "total_paid": {"type": "number"},
                    "total_owed": {"type": "number"},
                    "balance": {"type": "number"},
                },
            },
            "Settlement": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "amount": {"type": "number"},
                },
            },
            "SplitSettleResponse": {
                "type": "object",
                "properties": {
                    "currency": {"type": "string"},
                    "summary": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/ParticipantSummary"},
                    },
                    "settlements": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Settlement"},
                    },
                    "total_expenses": {"type": "number"},
                    "num_settlements": {"type": "integer"},
                },
            },
        },
    },
}


def lambda_handler(event, context):
    path = event.get("rawPath", "")

    if path == "/openapi.json":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(OPENAPI_SCHEMA),
        }

    if path == "/health":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"status": "ok"}),
        }

    headers = event.get("headers") or {}
    x_payment = headers.get("x-payment") or headers.get("X-Payment") or headers.get("X-PAYMENT") or ""

    if x_payment:
        # x402 flow: verify on-chain payment
        try:
            payment_data = json.loads(x_payment)
            tx_hash = payment_data.get("tx_hash", "")
            network = payment_data.get("network", "")
        except (json.JSONDecodeError, AttributeError):
            return _payment_required_response("malformed X-PAYMENT header")
        if not tx_hash or not network:
            return _payment_required_response("X-PAYMENT must include tx_hash and network")
        valid, error = _verify_payment(tx_hash, network)
        if not valid:
            return _payment_required_response(error)
    else:
        # Legacy API key flow
        api_key = _get_api_key()
        if api_key:
            provided = headers.get("x-api-key", "")
            if provided != api_key:
                return {
                    "statusCode": 403,
                    "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"error": "Forbidden: invalid or missing x-api-key"}),
                }
        else:
            # No API key configured and no payment header → request payment
            return _payment_required_response()

    try:
        body = json.loads(event.get("body") or "{}")
        result = split_settle(body)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result),
        }
    except ValueError as e:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(e)}),
        }
    except Exception:
        logger.exception("Unhandled error in lambda_handler")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"}),
        }


def split_settle(data: dict) -> dict:
    currency = data.get("currency")
    participants = data.get("participants", [])
    expenses = data.get("expenses", [])

    if not currency:
        raise ValueError("currency is required")
    if len(participants) < 2:
        raise ValueError("at least 2 participants required")
    if len(participants) > 20:
        raise ValueError("participants cannot exceed 20")
    if len(expenses) < 1:
        raise ValueError("at least 1 expense required")

    participant_set = set(participants)
    total_paid_cents = {p: 0 for p in participants}
    total_owed_cents = {p: 0 for p in participants}
    total_cents = 0

    for expense in expenses:
        paid_by = expense.get("paid_by")
        amount = expense.get("amount")
        split_among = expense.get("split_among", [])

        if not paid_by or paid_by not in participant_set:
            raise ValueError(f"Invalid paid_by: '{paid_by}'")
        if amount is None or amount <= 0:
            raise ValueError("amount must be > 0")
        if not split_among:
            raise ValueError("split_among cannot be empty")
        for p in split_among:
            if p not in participant_set:
                raise ValueError(f"'{p}' in split_among is not in participants")

        amount_cents = round(amount * 100)
        total_cents += amount_cents
        total_paid_cents[paid_by] += amount_cents

        n = len(split_among)
        share = amount_cents // n
        remainder = amount_cents % n

        for i, person in enumerate(split_among):
            total_owed_cents[person] += share + (1 if i < remainder else 0)

    balances = {p: total_paid_cents[p] - total_owed_cents[p] for p in participants}

    assert sum(balances.values()) == 0, "Balance checksum failed"

    summary = [
        {
            "participant": p,
            "total_paid": total_paid_cents[p] / 100,
            "total_owed": total_owed_cents[p] / 100,
            "balance": balances[p] / 100,
        }
        for p in participants
    ]

    settlements = _calculate_settlements(balances)

    return {
        "currency": currency,
        "summary": summary,
        "settlements": [
            {"from": s["from"], "to": s["to"], "amount": s["amount"] / 100}
            for s in settlements
        ],
        "total_expenses": total_cents / 100,
        "num_settlements": len(settlements),
    }


def _calculate_settlements(balances: dict) -> list:
    """Greedy: match largest debtor with largest creditor until all settled."""
    creditors = sorted(
        [[v, k] for k, v in balances.items() if v > 0], reverse=True
    )
    debtors = sorted(
        [[-v, k] for k, v in balances.items() if v < 0], reverse=True
    )

    settlements = []
    i = j = 0

    while i < len(creditors) and j < len(debtors):
        credit, creditor = creditors[i]
        debt, debtor = debtors[j]

        transfer = min(credit, debt)
        settlements.append({"from": debtor, "to": creditor, "amount": transfer})

        creditors[i][0] -= transfer
        debtors[j][0] -= transfer

        if creditors[i][0] == 0:
            i += 1
        if debtors[j][0] == 0:
            j += 1

    return settlements
