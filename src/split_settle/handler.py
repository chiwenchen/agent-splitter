import json
import logging
import os
import re
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

# Base Sepolia USDC contract for Phase A demo
SETTLEMENT_NETWORK        = "base-sepolia"
SETTLEMENT_TOKEN_CONTRACT = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
# ERC-20 transfer(address,uint256) selector
TRANSFER_SELECTOR = "a9059cbb"

_secret_cache: dict = {}
_HEX_CHARS = set("0123456789abcdefABCDEF")


def _get_secret(env_var: str, arn_var: str) -> str:
    """Get a secret from env var or Secrets Manager. Cached globally."""
    if env_var in _secret_cache:
        return _secret_cache[env_var]
    direct = os.environ.get(env_var, "")
    if direct:
        _secret_cache[env_var] = direct
        return direct
    arn = os.environ.get(arn_var, "")
    if arn:
        import boto3
        client = boto3.client("secretsmanager", region_name="ap-northeast-1")
        response = client.get_secret_value(SecretId=arn)
        _secret_cache[env_var] = response.get("SecretString", "")
    else:
        _secret_cache[env_var] = ""
    return _secret_cache[env_var]


def _validate_checksum_address(address: str) -> bool:
    """Validate EIP-55 mixed-case checksum encoding."""
    if not isinstance(address, str) or len(address) != 42 or address[:2] != "0x":
        return False
    addr_hex = address[2:]
    if not all(c in _HEX_CHARS for c in addr_hex):
        return False
    # Lazy import to avoid cold start penalty on non-groups calls
    from Crypto.Hash import keccak
    k = keccak.new(digest_bits=256)
    k.update(addr_hex.lower().encode("ascii"))
    hash_hex = k.hexdigest()
    for i, c in enumerate(addr_hex):
        if c in "0123456789":
            continue
        if int(hash_hex[i], 16) >= 8:
            if c != c.upper():
                return False
        else:
            if c != c.lower():
                return False
    return True


def _encode_transfer_calldata(to_address: str, amount_wei: int) -> str:
    """Encode ERC-20 transfer(address,uint256) calldata."""
    addr_padded = to_address[2:].lower().zfill(64)
    amount_padded = hex(amount_wei)[2:].zfill(64)
    return "0x" + TRANSFER_SELECTOR + addr_padded + amount_padded


def _rpc_call(method: str, params: list) -> dict:
    """Make a JSON-RPC call to Alchemy."""
    url = _get_secret("ALCHEMY_RPC_URL", "ALCHEMY_SECRET_ARN")
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


_GROUP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}[a-z0-9]$")


def _create_group(group_id: str, participants: list) -> dict:
    """Create a wallet group in DynamoDB. Returns summary dict."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        raise ValueError("GROUPS_TABLE not configured")

    if not group_id or not _GROUP_ID_RE.match(group_id):
        raise ValueError("group_id must be 2-64 lowercase alphanumeric + hyphens")

    if not participants or len(participants) < 2:
        raise ValueError("at least 2 participants required")
    if len(participants) > 20:
        raise ValueError("participants cannot exceed 20")

    for p in participants:
        name = p.get("name", "")
        wallet = p.get("wallet_address", "")
        if not name:
            raise ValueError("each participant must have a name")
        if not _validate_checksum_address(wallet):
            raise ValueError(f"invalid wallet address for {name}")

    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Check for duplicate group_id by trying to read the first participant
    existing = client.query(
        TableName=table,
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": {"S": f"GROUP#{group_id}"}},
        Limit=1,
    )
    if existing.get("Items"):
        raise GroupExistsError(f"group '{group_id}' already exists")

    for p in participants:
        client.put_item(
            TableName=table,
            Item={
                "PK": {"S": f"GROUP#{group_id}"},
                "SK": {"S": f"PARTICIPANT#{p['name']}"},
                "wallet_address": {"S": p["wallet_address"]},
                "created_at": {"S": created_at},
            },
        )

    return {"group_id": group_id, "participants": len(participants), "created_at": created_at}


class GroupExistsError(Exception):
    pass


def _get_group_participants(group_id: str) -> dict:
    """Query DynamoDB for group participants. Returns {name: wallet_address} dict."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        raise ValueError("GROUPS_TABLE not configured")

    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    response = client.query(
        TableName=table,
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": {"S": f"GROUP#{group_id}"}},
    )

    items = response.get("Items", [])
    if not items:
        return {}

    result = {}
    for item in items:
        sk = item["SK"]["S"]
        name = sk.replace("PARTICIPANT#", "", 1)
        result[name] = item["wallet_address"]["S"]
    return result


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
        "description": (
            "AI agent expense splitting with on-chain settlement execution. "
            "Calculate minimum transfers, then get ABI-encoded calldata to "
            "settle debts on Base Sepolia with USDC."
        ),
        "version": "2.0.0",
    },
    "servers": [
        {
            "url": "https://sfd9k548wj.execute-api.ap-northeast-1.amazonaws.com",
            "description": "Production",
        }
    ],
    "paths": {
        "/v1/groups": {
            "post": {
                "summary": "Create a wallet group",
                "description": "Register participant names with EIP-55 checksummed wallet addresses. Used by /v1/split_settle to generate on-chain settlement calldata.",
                "tags": ["Groups"],
                "security": [{"ApiKeyAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/CreateGroupRequest"},
                            "example": {
                                "group_id": "trip-tokyo-2026",
                                "participants": [
                                    {"name": "Alice", "wallet_address": "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"},
                                    {"name": "Bob", "wallet_address": "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"},
                                ],
                            },
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Group created",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CreateGroupResponse"},
                            }
                        },
                    },
                    "400": {"description": "Invalid input (bad wallet address, missing fields)"},
                    "409": {"description": "Group ID already exists"},
                },
            }
        },
        "/v1/split_settle": {
            "post": {
                "summary": "Calculate settlement plan (+ optional execution calldata)",
                "description": (
                    "Given participants and expenses, returns minimum transfers to settle all debts. "
                    "When group_id is provided, also returns ABI-encoded ERC-20 transfer calldata "
                    "for on-chain settlement on Base Sepolia."
                ),
                "tags": ["Settlement"],
                "security": [{"ApiKeyAuth": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/SplitSettleRequest"},
                            "examples": {
                                "basic": {
                                    "summary": "Basic split (no on-chain)",
                                    "value": {
                                        "currency": "TWD",
                                        "participants": ["Alice", "Bob", "Carol"],
                                        "expenses": [
                                            {"description": "Dinner", "paid_by": "Alice", "amount": 1200, "split_among": ["Alice", "Bob", "Carol"]},
                                            {"description": "Taxi", "paid_by": "Bob", "amount": 300, "split_among": ["Alice", "Bob", "Carol"]},
                                        ],
                                    },
                                },
                                "with_group": {
                                    "summary": "With group_id (returns calldata)",
                                    "value": {
                                        "currency": "USD",
                                        "group_id": "trip-tokyo-2026",
                                        "participants": ["Alice", "Bob"],
                                        "expenses": [
                                            {"description": "Hotel", "paid_by": "Alice", "amount": 200, "split_among": ["Alice", "Bob"]},
                                        ],
                                    },
                                },
                            },
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Settlement plan (with optional execution block)",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/SplitSettleResponse"}
                            }
                        },
                    },
                    "400": {"description": "Invalid request"},
                    "402": {"description": "Payment required (x402)"},
                    "403": {"description": "Invalid or missing API key"},
                },
            }
        },
        "/health": {
            "get": {
                "summary": "Health check",
                "tags": ["System"],
                "responses": {"200": {"description": "OK"}},
            }
        },
    },
    "components": {
        "securitySchemes": {
            "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "x-api-key"}
        },
        "schemas": {
            "GroupParticipant": {
                "type": "object",
                "required": ["name", "wallet_address"],
                "properties": {
                    "name": {"type": "string"},
                    "wallet_address": {"type": "string", "description": "EIP-55 checksummed Ethereum address"},
                },
            },
            "CreateGroupRequest": {
                "type": "object",
                "required": ["group_id", "participants"],
                "properties": {
                    "group_id": {"type": "string", "description": "Lowercase alphanumeric + hyphens, 2-64 chars"},
                    "participants": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/GroupParticipant"},
                        "minItems": 2,
                        "maxItems": 20,
                    },
                },
            },
            "CreateGroupResponse": {
                "type": "object",
                "properties": {
                    "group_id": {"type": "string"},
                    "participants": {"type": "integer"},
                    "created_at": {"type": "string", "format": "date-time"},
                },
            },
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
                    "group_id": {"type": "string", "description": "Optional: include to get on-chain execution calldata"},
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
            "Transfer": {
                "type": "object",
                "properties": {
                    "from_wallet": {"type": "string"},
                    "to_wallet": {"type": "string"},
                    "amount_wei": {"type": "string"},
                    "calldata": {"type": "string", "description": "ABI-encoded ERC-20 transfer(address,uint256)"},
                },
            },
            "ExecutionBlock": {
                "type": "object",
                "properties": {
                    "network": {"type": "string"},
                    "token_contract": {"type": "string"},
                    "transfers": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Transfer"},
                    },
                    "note": {"type": "string"},
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
                    "execution": {
                        "$ref": "#/components/schemas/ExecutionBlock",
                        "description": "Present only when group_id is provided",
                    },
                },
            },
        },
    },
}

SWAGGER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>SplitSettle API</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
  <style>
    body { margin: 0; background: #fafafa; }
    #swagger-ui .topbar { display: none; }
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({
      url: '/openapi.json',
      dom_id: '#swagger-ui',
      presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
      layout: 'BaseLayout',
      deepLinking: true,
      defaultModelsExpandDepth: 1,
      tryItOutEnabled: true,
    });
  </script>
</body>
</html>"""


_METHOD_NOT_ALLOWED = {
    "statusCode": 405,
    "headers": {"Content-Type": "application/json"},
    "body": json.dumps({"error": "Method Not Allowed"}),
}

_ROUTE_METHODS = {
    "/openapi.json": "GET",
    "/health": "GET",
    "/docs": "GET",
    "/v1/split_settle": "POST",
    "/v1/groups": "POST",
}


def lambda_handler(event, context):
    path = event.get("rawPath", "")
    method = event.get("requestContext", {}).get("http", {}).get("method", "")

    expected_method = _ROUTE_METHODS.get(path)
    if expected_method and method and method != expected_method:
        return _METHOD_NOT_ALLOWED

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

    if path == "/docs":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "text/html"},
            "body": SWAGGER_HTML,
        }

    if path == "/v1/groups":
        return _handle_groups(event)

    return _handle_split_settle(event)


def _handle_groups(event):
    """Handle POST /v1/groups — create a wallet group."""
    headers = event.get("headers") or {}

    # Auth: require API key for group creation
    api_key = _get_secret("API_KEY", "SECRET_ARN")
    if api_key:
        provided = headers.get("x-api-key", "")
        if provided != api_key:
            return {
                "statusCode": 403,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Forbidden: invalid or missing x-api-key"}),
            }

    try:
        body = json.loads(event.get("body") or "{}")
        result = _create_group(body.get("group_id", ""), body.get("participants", []))
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(result),
        }
    except GroupExistsError as e:
        return {
            "statusCode": 409,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(e), "code": "GROUP_EXISTS"}),
        }
    except ValueError as e:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(e)}),
        }
    except Exception:
        logger.exception("Unhandled error in _handle_groups")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"}),
        }


def _handle_split_settle(event):
    """Handle POST /v1/split_settle — calculate settlements."""
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
        api_key = _get_secret("API_KEY", "SECRET_ARN")
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
        logger.exception("Unhandled error in _handle_split_settle")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"}),
        }


def split_settle(data: dict) -> dict:
    currency = data.get("currency")
    participants = data.get("participants", [])
    expenses = data.get("expenses", [])
    group_id = data.get("group_id")

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

    result = {
        "currency": currency,
        "summary": summary,
        "settlements": [
            {"from": s["from"], "to": s["to"], "amount": s["amount"] / 100}
            for s in settlements
        ],
        "total_expenses": total_cents / 100,
        "num_settlements": len(settlements),
    }

    # When group_id is provided, add execution block with ABI-encoded calldata
    if group_id:
        wallet_map = _get_group_participants(group_id)
        if not wallet_map:
            raise ValueError(f"group '{group_id}' not found")

        # Validate all settlement participants exist in the group
        for s in settlements:
            for role in ("from", "to"):
                name = s[role]
                if name not in wallet_map:
                    raise ValueError(
                        f"participant '{name}' in expenses not found in group"
                    )

        transfers = []
        for s in settlements:
            from_wallet = wallet_map[s["from"]]
            to_wallet = wallet_map[s["to"]]
            # Convert settlement amount to USDC wei (6 decimals)
            amount_wei = round(s["amount"] / 100 * 1_000_000)
            transfers.append({
                "from_wallet": from_wallet,
                "to_wallet": to_wallet,
                "amount_wei": str(amount_wei),
                "calldata": _encode_transfer_calldata(to_wallet, amount_wei),
            })

        result["execution"] = {
            "network": SETTLEMENT_NETWORK,
            "token_contract": SETTLEMENT_TOKEN_CONTRACT,
            "transfers": transfers,
            "note": (
                "Calldata encodes ERC-20 transfer(address,uint256). "
                "Caller must sign and submit each transfer from the from_wallet."
            ),
        }

    return result


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
