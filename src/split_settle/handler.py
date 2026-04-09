import base64
import json
import logging
import os
import re
import secrets
import time
import urllib.parse
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
    # SECURITY: fail-closed in production. If the API key secret resolves to
    # empty inside a Lambda runtime, something is misconfigured and we MUST
    # NOT silently degrade to "no auth required".
    if (
        env_var == "API_KEY"
        and not _secret_cache[env_var]
        and os.environ.get("AWS_LAMBDA_FUNCTION_NAME")
    ):
        raise RuntimeError(
            "API_KEY secret is empty in Lambda runtime — refusing to start "
            "in unauthenticated mode"
        )
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
        if wallet and not _validate_checksum_address(wallet):
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
        item = {
            "PK": {"S": f"GROUP#{group_id}"},
            "SK": {"S": f"PARTICIPANT#{p['name']}"},
            "created_at": {"S": created_at},
        }
        if p.get("wallet_address"):
            item["wallet_address"] = {"S": p["wallet_address"]}
        client.put_item(TableName=table, Item=item)

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
        result[name] = item.get("wallet_address", {}).get("S", "")
    return result


def _generate_share_id() -> str:
    """Generate an 8-char URL-safe share ID."""
    return secrets.token_urlsafe(6)[:8]


def _save_share(share_id: str, request_body: dict, result: dict) -> None:
    """Save a shared split result to DynamoDB."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        raise ValueError("GROUPS_TABLE not configured")
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    ttl = int(time.time()) + 86400 * 30  # 30 days
    client.put_item(
        TableName=table,
        Item={
            "PK": {"S": f"SHARE#{share_id}"},
            "SK": {"S": "RESULT"},
            "request_body": {"S": json.dumps(request_body)},
            "result": {"S": json.dumps(result)},
            "created_at": {"S": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
            "ttl_expiry": {"N": str(ttl)},
        },
    )


def _get_share(share_id: str) -> dict:
    """Get a shared split result from DynamoDB. Returns dict or None."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        return None
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    response = client.get_item(
        TableName=table,
        Key={"PK": {"S": f"SHARE#{share_id}"}, "SK": {"S": "RESULT"}},
    )
    item = response.get("Item")
    if not item:
        return None
    return {
        "request_body": json.loads(item["request_body"]["S"]),
        "result": json.loads(item["result"]["S"]),
        "created_at": item["created_at"]["S"],
        "ttl_expiry": int(item["ttl_expiry"]["N"]),
    }


ACCOUNT_TEXT_MAX = 500


def _get_accounts(share_id: str) -> dict:
    """Return {participant_name: account_text} for a share. Empty dict if none."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        return {}
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    response = client.query(
        TableName=table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"SHARE#{share_id}"},
            ":sk": {"S": "ACCOUNT#"},
        },
    )
    out = {}
    for item in response.get("Items", []):
        name = item["SK"]["S"].replace("ACCOUNT#", "", 1)
        out[name] = item.get("account_text", {}).get("S", "")
    return out


def _save_account(share_id: str, participant: str, account_text: str,
                  device_id: str, ttl_expiry: int) -> None:
    """Upsert an ACCOUNT# row. Caller must have validated inputs."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        raise ValueError("GROUPS_TABLE not configured")
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    client.put_item(
        TableName=table,
        Item={
            "PK": {"S": f"SHARE#{share_id}"},
            "SK": {"S": f"ACCOUNT#{participant}"},
            "account_text": {"S": account_text},
            "updated_at": {"S": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
            "updated_by": {"S": device_id or ""},
            "ttl_expiry": {"N": str(ttl_expiry)},
        },
    )


def _delete_account(share_id: str, participant: str) -> None:
    """Delete an ACCOUNT# row. No-op if GROUPS_TABLE unset (local/tests)."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        return
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    client.delete_item(
        TableName=table,
        Key={
            "PK": {"S": f"SHARE#{share_id}"},
            "SK": {"S": f"ACCOUNT#{participant}"},
        },
    )


def _bad_request(msg: str) -> dict:
    return {
        "statusCode": 400,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": msg}),
    }


def _handle_share_accounts(event):
    """Handle /v1/share/{id}/accounts[/{participant}] — public, no API key."""
    path = event.get("rawPath", "")
    method = (event.get("requestContext", {})
              .get("http", {}).get("method", "GET")).upper()

    rest = path.split("/v1/share/", 1)[-1]
    parts = rest.split("/")
    if len(parts) < 2 or parts[1] != "accounts":
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "not found"}),
        }
    share_id = parts[0]
    participant = urllib.parse.unquote(parts[2]) if len(parts) >= 3 and parts[2] else None

    share = _get_share(share_id)
    if not share or share["ttl_expiry"] < time.time():
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "share not found"}),
        }

    if method == "GET" and participant is None:
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(_get_accounts(share_id)),
        }

    if method == "PUT" and participant is not None:
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _bad_request("invalid json")
        account_text = body.get("account_text", "")
        if not isinstance(account_text, str):
            return _bad_request("account_text must be a string")
        if len(account_text) > ACCOUNT_TEXT_MAX:
            return _bad_request(f"account_text exceeds {ACCOUNT_TEXT_MAX} chars")
        participants = share["request_body"].get("participants", [])
        if participant not in participants:
            return _bad_request("participant not in this share")
        device_id = (event.get("headers") or {}).get("x-device-id", "")
        _save_account(share_id, participant, account_text, device_id,
                      share["ttl_expiry"])
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"ok": True}),
        }

    if method == "DELETE" and participant is not None:
        participants = share["request_body"].get("participants", [])
        if participant not in participants:
            return _bad_request("participant not in this share")
        _delete_account(share_id, participant)
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"ok": True}),
        }

    return {
        "statusCode": 405,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": "method not allowed"}),
    }


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
            "url": "https://split.redarch.dev",
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


# Load JS from file at module init (avoids Python string escaping issues with backticks)
_APP_JS_PATH = os.path.join(os.path.dirname(__file__), "app.js")
with open(_APP_JS_PATH, "r") as _f:
    _APP_JS = _f.read()

_APP_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Split Senpai - 分帳仙貝</title>
  <meta name="description" content="Split expenses with friends. No registration, no app download. Share a link and settle up.">
  <style>
    :root {
      --layer-0:#d5d0c8; --layer-1:#2d4a4a; --layer-2:#1e3636;
      --accent:#e8a84c; --accent-dark:#c88830;
      --text-on-dark:#e0d5c4; --text-muted:#8aaa9e; --text-dim:#5a7a70;
      --border:#3a5e5e;
      --r-card:19px; --r-outer:28px; --r-sm:12px;
      --neu-out:4px 4px 8px rgba(10,30,30,0.4),-2px -2px 4px rgba(60,100,100,0.1);
      --neu-in:inset -3px 3px 6px rgba(10,30,30,0.5),inset 3px -3px 6px rgba(60,100,100,0.15);
    }
    * { margin:0;padding:0;box-sizing:border-box; }
    button,select,input { touch-action:manipulation; }
    body { font-family:'Inter',-apple-system,system-ui,sans-serif; background:var(--layer-0);
           min-height:100vh; display:flex; justify-content:center; padding:16px; }
    .container { width:100%; max-width:420px; background:var(--layer-1); border-radius:var(--r-outer);
                 padding:28px; color:var(--text-on-dark); box-shadow:12px 12px 12px rgba(30,50,50,0.4);
                 margin:0 auto; }
    @media(max-width:460px) { body{padding:0;} .container{border-radius:0;min-height:100vh;} }
    .header-row { display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:24px; }
    h1 { font-size:26px;font-weight:800;color:var(--accent); }
    .subtitle { font-size:13px;color:var(--text-muted);margin-top:2px; }
    .lang-btn { background:var(--layer-2);border:none;color:var(--text-muted);border-radius:10px;
                padding:6px 10px;font-size:12px;font-weight:600;cursor:pointer;box-shadow:var(--neu-out); }
    .section { margin-bottom:20px; }
    .section-title { font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;
                     letter-spacing:1px;margin-bottom:10px; }
    .chip { display:inline-flex;align-items:center;gap:7px;background:var(--layer-1);border:none;
            border-radius:20px;padding:4px 14px 4px 4px;margin:0 6px 6px 0;font-size:13px;font-weight:600;
            box-shadow:var(--neu-out); }
    .chip .av { width:26px;height:26px;border-radius:50%;overflow:hidden;display:flex;align-items:center;justify-content:center; }
    .chip button { background:none;border:none;color:var(--text-dim);cursor:pointer;margin-left:2px;font-size:13px; }
    input,select { background:var(--layer-2);border:none;color:var(--text-on-dark);border-radius:var(--r-sm);
                   padding:11px 14px;font-size:14px;width:100%;outline:none;box-shadow:var(--neu-in); }
    input:focus { box-shadow:var(--neu-in),0 0 0 2px rgba(232,168,76,0.3); }
    input::placeholder { color:var(--text-dim); }
    select { box-shadow:var(--neu-out);padding:8px;font-size:13px; }
    .row { display:flex;gap:8px;margin-bottom:8px; }
    .row > * { flex:1; }
    .btn { background:var(--accent);color:var(--layer-2);border:none;border-radius:var(--r-sm);
           padding:11px 16px;font-size:14px;font-weight:600;width:100%;cursor:pointer;
           box-shadow:var(--neu-out);transition:box-shadow 0.15s; }
    .btn:active { box-shadow:var(--neu-in); }
    .btn:disabled { background:var(--layer-2);color:var(--text-dim);cursor:not-allowed;box-shadow:var(--neu-in); }
    .btn-outline { background:var(--layer-1);color:var(--text-muted);border:none;border-radius:var(--r-sm);
                   box-shadow:var(--neu-out); }
    .expense-card { background:var(--layer-2);border:none;border-radius:var(--r-card);padding:14px 16px;
                    margin-bottom:10px;display:flex;justify-content:space-between;align-items:center;
                    box-shadow:var(--neu-in); }
    .expense-card .left { display:flex;align-items:center;gap:10px; }
    .expense-card .av { width:34px;height:34px;border-radius:50%;overflow:hidden;
                        display:flex;align-items:center;justify-content:center;flex-shrink:0; }
    .expense-card .desc { font-size:14px;font-weight:500; }
    .expense-card .amount { color:var(--accent);font-weight:700;font-size:15px;white-space:nowrap; }
    .expense-card .meta { font-size:11px;color:var(--text-dim);margin-top:3px; }
    .expense-card button { background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:16px; }
    .tag { display:inline-block;font-size:10px;font-weight:600;padding:2px 7px;border-radius:8px;margin-right:3px; }
    .tag-paid { background:#1a3a5a;color:#7ab8e8; }
    .tag-split { background:#4a3020;color:#e8b080; }
    .divider { border:none;height:2px;margin:24px 0 18px;
               background:linear-gradient(90deg,transparent,var(--accent),var(--text-muted),var(--accent),transparent); }
    .receipt-box { background:var(--layer-2);border-radius:var(--r-card);padding:16px 20px;
                   margin-bottom:12px;box-shadow:var(--neu-in);position:relative; }
    .receipt-title { text-align:center;margin-bottom:12px; }
    .receipt-title span { background:var(--layer-1);color:var(--accent);padding:4px 16px;
                          border-radius:8px;font-size:12px;font-weight:700;letter-spacing:0.5px; }
    .receipt-cutout { position:relative;height:2px;margin:0 -20px 14px;
                      background:repeating-linear-gradient(90deg,#3a5e5e 0,#3a5e5e 8px,transparent 8px,transparent 16px); }
    .receipt-cutout::before,.receipt-cutout::after { content:'';position:absolute;top:-8px;width:16px;height:16px;
                      border-radius:50%;background:var(--layer-1); }
    .receipt-cutout::before { left:-8px; }
    .receipt-cutout::after { right:-8px; }
    .result-item { background:linear-gradient(135deg,var(--accent),var(--accent-dark));color:var(--layer-2);
                   border-radius:var(--r-sm);padding:12px 16px;margin-bottom:8px;
                   display:flex;justify-content:space-between;align-items:center;box-shadow:var(--neu-out); }
    .result-item .left { display:flex;align-items:center;gap:8px; }
    .result-item .av { width:28px;height:28px;border-radius:50%;overflow:hidden;
                       border:2px solid rgba(255,255,255,0.5);display:flex;align-items:center;
                       justify-content:center;flex-shrink:0; }
    .result-from { font-weight:700;color:#5a2020; }
    .result-to { font-weight:700;color:#1a4a3a; }
    .result-arrow { margin:0 3px;color:#6a4a10; }
    .result-amount { font-weight:800;font-size:16px; }
    .summary-line { text-align:center;background:var(--layer-2);padding:10px 16px;border-radius:var(--r-sm);
                    font-size:12px;color:var(--text-muted);margin-top:10px;box-shadow:var(--neu-in); }
    .check { color:var(--accent); }
    .share-result { text-align:center;margin-top:16px;padding:16px;background:var(--layer-2);
                    border:1px solid var(--border);border-radius:var(--r-card); }
    .share-result a { color:var(--accent);word-break:break-all; }
    .error { color:#e88060;font-size:13px;margin-top:8px;text-align:center; }
    .confirm { background:var(--layer-2);border:none;border-radius:28px;padding:6px;
               display:flex;align-items:center;margin-top:16px;box-shadow:var(--neu-in);
               overflow:hidden;position:relative;width:100%;user-select:none;-webkit-user-select:none;
               touch-action:none; }
    .confirm-bg { position:absolute;left:0;top:0;bottom:0;right:0;border-radius:28px;
                  background:linear-gradient(90deg,var(--accent),var(--accent-dark));
                  transition:opacity 0.1s; }
    .confirm-btn { background:var(--layer-1);color:var(--accent);border:none;border-radius:22px;
                   padding:12px 20px;font-size:14px;font-weight:700;cursor:grab;z-index:2;
                   box-shadow:var(--neu-out);white-space:nowrap;transition:none;min-width:140px;text-align:center; }
    .confirm-btn:active { cursor:grabbing; }
    .dot-loading span { animation:dotBounce 1.2s infinite; display:inline-block; }
    .dot-loading span:nth-child(2) { animation-delay:0.2s; }
    .dot-loading span:nth-child(3) { animation-delay:0.4s; }
    @keyframes dotBounce { 0%,80%,100%{transform:translateY(0)} 40%{transform:translateY(-4px)} }
    .confirm-arrows { display:flex;align-items:center;margin-left:12px; }
    .arrow-icon { width:20px;height:20px;color:var(--text-dim);margin-left:-6px;
                  animation:arrowFlow 2s infinite;opacity:0; }
    .arrow-icon:nth-child(1){animation-delay:0s} .arrow-icon:nth-child(2){animation-delay:.15s}
    .arrow-icon:nth-child(3){animation-delay:.3s} .arrow-icon:nth-child(4){animation-delay:.45s}
    .arrow-icon:nth-child(5){animation-delay:.6s}
    @keyframes arrowFlow{0%{opacity:0;transform:translateX(-8px)}40%{opacity:.7}100%{opacity:0;transform:translateX(10px)}}
    .checkbox-group { display:flex;flex-wrap:wrap;gap:8px;margin-top:6px; }
    .checkbox-group label { display:flex;align-items:center;gap:4px;font-size:13px;
                            background:var(--layer-1);border:none;border-radius:10px;padding:5px 10px;
                            cursor:pointer;box-shadow:var(--neu-out); }
    .checkbox-group input[type=checkbox] { width:auto;accent-color:var(--accent); }
    .add-form { background:var(--layer-2);border:none;border-radius:var(--r-card);padding:14px;
                margin-bottom:8px;box-shadow:var(--neu-in); }
    @keyframes cardIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}
    .expense-card-new { animation:cardIn 0.3s ease-out; }
    @keyframes shimmerSweep{0%{left:-100%}60%{left:120%}100%{left:120%}}
    @keyframes glowPulse{0%,100%{box-shadow:0 0 8px rgba(232,168,76,0.2),0 0 20px rgba(232,168,76,0.1)}50%{box-shadow:0 0 16px rgba(232,168,76,0.4),0 0 40px rgba(232,168,76,0.15)}}
    .btn-add-hint { position:relative;overflow:hidden;color:var(--accent)!important;
                    background:linear-gradient(135deg,rgba(232,168,76,0.06),rgba(232,168,76,0.12))!important;
                    animation:glowPulse 2s ease-in-out infinite; }
    .btn-add-hint::before { content:'🧾 💳 🍜 🚕 🍺 🧾 💳 🍜 🚕 🍺';position:absolute;top:50%;
                            transform:translateY(-50%);white-space:nowrap;font-size:14px;letter-spacing:12px;
                            opacity:0.2;animation:receiptFloat 8s linear infinite;pointer-events:none; }
    .btn-add-hint::after { content:'';position:absolute;top:0;left:-100%;width:50%;height:100%;
                           background:linear-gradient(90deg,transparent,rgba(255,255,255,0.3),transparent);
                           animation:shimmerSweep 2.5s ease-in-out infinite;pointer-events:none; }
    @keyframes receiptFloat{0%{left:100%}100%{left:-200%}}
    button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
    /* Calculator keypad */
    .calc-pad { display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-top:6px;margin-bottom:8px; }
    .calc-key { background:var(--layer-1);color:var(--text-on-dark);border:none;border-radius:8px;
                padding:10px 0;font-size:16px;font-weight:600;cursor:pointer;
                box-shadow:var(--neu-out);transition:transform 0.1s;
                touch-action:manipulation;-webkit-tap-highlight-color:transparent; }
    .calc-key:active, .calc-key-pressed { transform:scale(0.88);box-shadow:var(--neu-in);background:var(--layer-2);transition:transform 0.05s; }
    .calc-key-op { color:var(--accent); }
    .calc-key-eq { background:var(--accent);color:var(--layer-2); }
    .calc-key-del { font-size:13px;color:var(--text-muted); }
    /* Copy success flash */
    @keyframes copyFlash { 0%{transform:scale(1)} 50%{transform:scale(1.05)} 100%{transform:scale(1)} }
    .btn-copied { background:#4a8a6a!important;animation:copyFlash 0.3s ease; }
    /* Haptic shake */
    @keyframes hapticShake {
      0%,100% { transform:translateX(0); }
      20% { transform:translateX(-1px); }
      40% { transform:translateX(1px); }
      60% { transform:translateX(-0.5px); }
      80% { transform:translateX(0); }
    }
    .haptic { animation:hapticShake 0.2s ease !important; }
    /* Golden shimmer sweep on input border */
    .input-orbit { position:relative;border-radius:14px;padding:2px;
      background:var(--border);overflow:hidden; }
    .input-orbit::before { content:'';position:absolute;top:0;left:-100%;width:60%;height:100%;
      background:linear-gradient(90deg,transparent,#e8a84c,#ffd080,#e8a84c,transparent);
      animation:shimmerSweep 3.5s ease-in-out infinite; }
    .input-orbit:focus-within { background:#e8a84c; }
    .input-orbit:focus-within::before { animation:none;display:none; }
    .input-orbit input { position:relative;z-index:1;border-radius:12px; }
  </style>
  <script type="importmap">{"imports":{"preact":"https://esm.sh/preact@10.25.4","preact/hooks":"https://esm.sh/preact@10.25.4/hooks","htm/preact":"https://esm.sh/htm@3.1.1/preact?external=preact","react":"https://esm.sh/preact@10.25.4/compat","boring-avatars":"https://esm.sh/boring-avatars@1?external=react"}}</script>
</head>
<body>
  <div class="container" id="app"></div>
  <script type="module">
__APP_JS_PLACEHOLDER__
  </script>
</body>
</html>"""

# Build APP_HTML by injecting the JS file content
APP_HTML = _APP_HTML_TEMPLATE.replace("__APP_JS_PLACEHOLDER__", _APP_JS)

_DEAD_CODE_REMOVED = """
      if (participants.length < 2 || expenses.length === 0) return null;
      const pSet = new Set(participants);
      const paid = Object.fromEntries(participants.map(p => [p, 0]));
      const owed = Object.fromEntries(participants.map(p => [p, 0]));
      let total = 0;
      for (const e of expenses) {
        if (!pSet.has(e.paid_by) || e.amount <= 0 || e.split_among.length === 0) continue;
        const cents = Math.round(e.amount * 100);
        total += cents;
        paid[e.paid_by] += cents;
        const share = Math.floor(cents / e.split_among.length);
        const rem = cents % e.split_among.length;
        e.split_among.forEach((p, i) => { if (pSet.has(p)) owed[p] += share + (i < rem ? 1 : 0); });
      }
      const bal = Object.fromEntries(participants.map(p => [p, paid[p] - owed[p]]));
      const creds = participants.filter(p => bal[p] > 0).map(p => [bal[p], p]).sort((a,b) => b[0]-a[0]);
      const debts = participants.filter(p => bal[p] < 0).map(p => [-bal[p], p]).sort((a,b) => b[0]-a[0]);
      const settlements = [];
      let i = 0, j = 0;
      while (i < creds.length && j < debts.length) {
        const t = Math.min(creds[i][0], debts[j][0]);
        settlements.push({ from: debts[j][1], to: creds[i][1], amount: t / 100 });
        creds[i][0] -= t; debts[j][0] -= t;
        if (creds[i][0] === 0) i++;
        if (debts[j][0] === 0) j++;
      }
      return { currency, total: total/100, settlements,
               summary: participants.map(p => ({ name: p, paid: paid[p]/100, owed: owed[p]/100, balance: bal[p]/100 })) };
    }

    const ZERO_DEC = new Set(['TWD','JPY','KRW','VND','IDR']);
    function fmtAmt(c, n) { const d=ZERO_DEC.has(c)?0:2; return Number(n).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d}); }

    function App() {
      const [participants, setP] = useState(['']);
      const [expenses, setE] = useState([]);
      const [currency, setCurrency] = useState(localStorage.getItem('ss_currency') || 'TWD');
      const [newName, setNewName] = useState('');
      const [showForm, setShowForm] = useState(false);
      const [formDesc, setFormDesc] = useState('');
      const [formAmt, setFormAmt] = useState('');
      const [formPayer, setFormPayer] = useState('');
      const [formSplit, setFormSplit] = useState([]);
      const [shareUrl, setShareUrl] = useState('');
      const [sharing, setSharing] = useState(false);
      const [error, setError] = useState('');
      const names = participants.filter(p => p.trim());
      const result = splitSettle(names, expenses, currency);

      function addName() {
        if (!newName.trim() || names.includes(newName.trim())) return;
        setP([...participants.filter(p=>p.trim()), newName.trim(), '']);
        setNewName('');
      }
      function removeName(n) {
        setP(participants.filter(p => p !== n));
        setE(expenses.filter(e => e.paid_by !== n && !e.split_among.includes(n)));
      }
      function openForm() {
        setFormDesc(''); setFormAmt(''); setFormPayer(names[0] || '');
        setFormSplit([...names]); setShowForm(true);
      }
      function addExpense() {
        const amt = parseFloat(formAmt);
        if (!amt || amt <= 0 || !formPayer || formSplit.length === 0) return;
        setE([...expenses, { description: formDesc || '', paid_by: formPayer, amount: amt, split_among: [...formSplit] }]);
        setShowForm(false);
      }
      function removeExpense(i) { setE(expenses.filter((_,idx) => idx !== i)); setShareUrl(''); }
      function changeCurrency(c) { setCurrency(c); localStorage.setItem('ss_currency', c); }
      function toggleSplit(name) {
        setFormSplit(formSplit.includes(name) ? formSplit.filter(n=>n!==name) : [...formSplit, name]);
      }

      async function share() {
        if (!result || result.settlements.length === 0) return;
        setSharing(true); setError(''); setShareUrl('');
        try {
          const body = { currency, participants: names,
            expenses: expenses.map(e => ({ description: e.description, paid_by: e.paid_by,
              amount: e.amount, split_among: e.split_among })) };
          const res = await fetch('/v1/share', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) });
          if (!res.ok) { const d = await res.json().catch(()=>({})); throw new Error(d.error || 'Failed'); }
          const data = await res.json();
          setShareUrl(window.location.origin + data.url);
        } catch (e) { setError(e.message); }
        setSharing(false);
      }
      async function copyLink() {
        try { await navigator.clipboard.writeText(shareUrl); } catch(e) {}
      }
      function webShare() {
        if (navigator.share) navigator.share({ title: 'SplitSettle', text: 'Check our expense split!', url: shareUrl });
      }

      return html`
        <h1>SplitSettle</h1>
        <div class="subtitle">Split expenses instantly. No registration needed.</div>

        <div class="section">
          <div class="section-title">Participants</div>
          <div>
            ${names.map(n => html`<span class="chip" key=${n}>${n}<button onClick=${()=>removeName(n)}>x</button></span>`)}
          </div>
          <div class="row" style="margin-top:8px">
            <input placeholder="Add a name..." value=${newName} onInput=${e=>setNewName(e.target.value)}
              onKeyDown=${e => e.key==='Enter' && addName()} />
            <button class="btn btn-outline" style="flex:0;padding:10px 16px" onClick=${addName}>+</button>
          </div>
        </div>

        <div class="section">
          <div class="row">
            <div class="section-title" style="flex:1;margin:0;line-height:28px">Expenses</div>
            <select style="flex:0;width:80px;text-align:center" value=${currency} onChange=${e=>changeCurrency(e.target.value)}>
              <option>TWD</option><option>USD</option><option>JPY</option><option>EUR</option>
              <option>GBP</option><option>CNY</option><option>KRW</option><option>THB</option>
            </select>
          </div>
          ${expenses.map((e,i) => html`
            <div class="expense-card" key=${i}>
              <div>
                <div class="desc">${e.description || 'Expense'}</div>
                <div class="meta">${e.paid_by} paid · split ${e.split_among.length} ways</div>
              </div>
              <div style="display:flex;align-items:center;gap:12px">
                <span class="amount">${currency} ${fmtAmt(currency, e.amount)}</span>
                <button onClick=${()=>removeExpense(i)}>x</button>
              </div>
            </div>
          `)}
          ${showForm ? html`
            <div class="add-form">
              <input placeholder="Description (optional)" value=${formDesc} onInput=${e=>setFormDesc(e.target.value)} style="margin-bottom:8px" />
              <input placeholder="Amount" inputmode="decimal" value=${formAmt} onInput=${e=>setFormAmt(e.target.value)} style="margin-bottom:8px" />
              <select value=${formPayer} onChange=${e=>setFormPayer(e.target.value)} style="margin-bottom:8px">
                ${names.map(n => html`<option key=${n}>${n}</option>`)}
              </select>
              <div class="section-title" style="margin-top:4px">Split among</div>
              <div class="checkbox-group">
                ${names.map(n => html`<label key=${n}><input type="checkbox" checked=${formSplit.includes(n)} onChange=${()=>toggleSplit(n)} />${n}</label>`)}
              </div>
              <div class="row" style="margin-top:10px">
                <button class="btn" onClick=${addExpense}>Add</button>
                <button class="btn btn-outline" onClick=${()=>setShowForm(false)}>Cancel</button>
              </div>
            </div>
          ` : html`<button class="btn btn-outline" onClick=${openForm} disabled=${names.length<2}>+ Add Expense</button>`}
        </div>

        ${result && result.settlements.length > 0 ? html`
          <hr class="divider" />
          <div class="section">
            <div class="section-title">Settlement</div>
            ${result.settlements.map(s => html`
              <div class="result-item">
                <span class="result-from">${s.from}</span> owes
                <span class="result-to"> ${s.to}</span>
                <span class="result-amount">${currency} ${fmtAmt(currency, s.amount)}</span>
              </div>
            `)}
            <div class="summary-line">
              ${currency} ${fmtAmt(currency, result.total)} total · ${result.settlements.length} transfer${result.settlements.length>1?'s':''} to settle <span class="check">✓</span>
            </div>
            ${shareUrl ? html`
              <div class="share-result">
                <div style="margin-bottom:8px">Link created!</div>
                <a href=${shareUrl}>${shareUrl}</a>
                <div class="row" style="margin-top:12px">
                  <button class="btn" onClick=${copyLink}>Copy Link</button>
                  ${navigator.share ? html`<button class="btn btn-outline" onClick=${webShare}>Share</button>` : ''}
                </div>
                <div style="margin-top:8px;font-size:12px;color:#666">Valid for 30 days</div>
              </div>
            ` : html`
              <button class="btn btn-share" onClick=${share} disabled=${sharing}>
                ${sharing ? 'Generating...' : 'Share Results'}
              </button>
            `}
            ${error ? html`<div class="error">${error}</div>` : ''}
          </div>
        ` : result && result.settlements.length === 0 && expenses.length > 0 ? html`
          <hr class="divider" />
          <div class="summary-line">Everyone is settled up! <span class="check">✓</span></div>
        ` : ''}

        <div style="text-align:center;margin-top:40px;font-size:11px;color:#444">
          Powered by Redarch
        </div>
      `;
    }

"""  # end _DEAD_CODE_REMOVED

NOT_FOUND_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Split Senpai - Not Found</title>
  <style>
    * { margin:0;padding:0;box-sizing:border-box; }
    body { font-family:'Inter',-apple-system,system-ui,sans-serif; background:#d5d0c8;
           display:flex;justify-content:center;align-items:center;min-height:100vh;padding:16px; }
    .card { background:#2d4a4a;border-radius:28px;padding:40px;text-align:center;color:#e0d5c4;
            box-shadow:12px 12px 12px rgba(30,50,50,0.4);max-width:380px;width:100%; }
    h2 { color:#e8a84c;font-size:20px;margin-bottom:8px; }
    p { color:#8aaa9e;margin-bottom:24px;font-size:14px; }
    a { display:inline-block;background:linear-gradient(135deg,#e8a84c,#c88830);color:#1e3636;
        text-decoration:none;padding:12px 24px;border-radius:12px;font-weight:700;font-size:14px;
        box-shadow:4px 4px 8px rgba(10,30,30,0.4),-2px -2px 4px rgba(60,100,100,0.1); }
  </style>
</head>
<body>
  <div class="card">
    <h2>Oops! Not found</h2>
    <p>This split has expired or doesn't exist.</p>
    <a href="/">Create a new split →</a>
  </div>
</body>
</html>"""

SHARE_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Split Senpai - {{title}}</title>
  <meta property="og:title" content="{{og_title}}" />
  <meta property="og:description" content="{{og_desc}}" />
  <meta property="og:type" content="website" />
  <meta name="apple-itunes-app" content="app-id=TODO_APPLE_APP_ID" />
  <style>
    * { margin:0;padding:0;box-sizing:border-box; }
    body { font-family:'Inter',-apple-system,system-ui,sans-serif; background:#d5d0c8;
           min-height:100vh; display:flex; justify-content:center; padding:16px; }
    .phone { width:100%;max-width:420px;background:#2d4a4a;border-radius:28px;padding:24px;
             color:#e0d5c4;box-shadow:12px 12px 12px rgba(30,50,50,0.4);margin:0 auto;height:fit-content; }
    @media(max-width:460px){body{padding:0}.phone{border-radius:0;min-height:100vh}}
    h1 { font-size:22px;font-weight:800;color:#e8a84c;margin-bottom:2px; }
    .date { font-size:11px;color:#5a7a70;margin-bottom:14px; }
    .total-line { font-size:12px;color:#8aaa9e;margin-bottom:14px; }
    .divider { border:none;height:2px;margin:16px 0;
               background:linear-gradient(90deg,transparent,#e8a84c,#8aaa9e,#e8a84c,transparent); }

    /* Identity card */
    .identity-card { background:linear-gradient(135deg,#1e3636,#234040);border-radius:14px;padding:14px 16px;
                     margin-bottom:10px;
                     box-shadow:inset -2px 2px 5px rgba(10,30,30,0.5),inset 2px -2px 5px rgba(60,100,100,0.15); }
    .id-row { display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:6px;flex-wrap:wrap; }
    .id-name { font-size:16px;font-weight:700;color:#e8a84c;min-width:0;flex:1;
               overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
    .id-edit { background:transparent;border:1px solid #e8a84c;color:#e8a84c;
               padding:4px 10px;border-radius:8px;font-size:11px;cursor:pointer;font-weight:600;
               flex-shrink:0;white-space:nowrap;font-family:inherit; }
    .id-summary { font-size:12px;line-height:1.6; }
    .id-summary .owed { color:#7fc69a;font-weight:700; }
    .id-summary .owes { color:#d96848;font-weight:700; }
    .id-summary > div { display:block; }

    /* Account editor (expandable inside identity card) */
    .acct-editor { margin-top:10px;padding-top:10px;border-top:1px solid rgba(90,122,112,0.3);display:none;
                   flex-direction:column;gap:6px; }
    .acct-editor.open { display:flex; }
    .acct-editor textarea { width:100%;padding:8px;border-radius:8px;border:none;
                            background:#2d4a4a;color:#e0d5c4;font-family:inherit;font-size:13px;
                            resize:vertical;box-sizing:border-box;min-height:60px; }
    .acct-editor button { padding:6px 14px;border:none;border-radius:8px;
                          background:linear-gradient(135deg,#e8a84c,#c88830);color:#1e3636;
                          font-weight:700;font-size:12px;cursor:pointer;align-self:flex-start;
                          font-family:inherit; }
    .acct-editor .status { font-size:11px;color:#5a7a70; }

    /* View toggle */
    .view-toggle { display:flex;justify-content:flex-end;margin:4px 0 8px; }
    .view-toggle button { background:transparent;border:none;color:#8aaa9e;
                          font-size:11px;cursor:pointer;text-decoration:underline;font-family:inherit; }

    /* Settlement rows */
    @keyframes slideIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
    .settlement { background:linear-gradient(135deg,#e8a84c,#c88830);color:#1e3636;
                  border-radius:12px;padding:10px 14px;margin-bottom:8px;
                  box-shadow:4px 4px 8px rgba(10,30,30,0.4),-2px -2px 4px rgba(60,100,100,0.1);
                  animation:slideIn 0.3s ease-out both;animation-delay:calc(var(--i,0)*0.1s);
                  transition:all 0.35s cubic-bezier(0.4,0,0.2,1);
                  max-height:200px;overflow:hidden;opacity:1;transform:translateX(0); }
    .settlement.hidden { max-height:0;opacity:0;transform:translateX(-40px);margin-bottom:0;
                         padding-top:0;padding-bottom:0; }
    .sett-main { display:flex;justify-content:space-between;align-items:center;font-size:14px; }
    .from { font-weight:700;color:#5a2020; }
    .to { font-weight:700;color:#1a4a3a; }
    .amount { font-weight:800;font-size:15px; }
    .payee-account { margin-top:6px;padding-top:6px;border-top:1px dashed rgba(30,54,54,0.3);
                     font-size:11px;display:flex;align-items:center;gap:6px;flex-wrap:wrap;color:#1e3636; }
    .payee-account code { background:rgba(30,54,54,0.15);padding:2px 6px;border-radius:5px;
                          font-family:'Menlo',monospace;color:#1e3636;word-break:break-all; }
    .payee-account .copy-btn { padding:2px 8px;font-size:10px;border:1px solid #1e3636;
                               background:transparent;border-radius:5px;cursor:pointer;color:#1e3636;font-weight:600; }
    .payee-account .muted { color:rgba(30,54,54,0.55);font-style:italic; }

    .summary { text-align:center;background:#1e3636;padding:8px 14px;border-radius:10px;
               font-size:11px;color:#8aaa9e;margin-top:10px;
               box-shadow:inset -3px 3px 6px rgba(10,30,30,0.5),inset 3px -3px 6px rgba(60,100,100,0.15); }
    .check { color:#e8a84c; }

    /* Bill details (collapsible) */
    details.bill-details { background:#1e3636;border-radius:12px;padding:10px 14px;margin-top:10px;
                           box-shadow:inset -2px 2px 5px rgba(10,30,30,0.5); }
    details.bill-details summary { font-size:12px;color:#e8a84c;font-weight:700;cursor:pointer;
                                   list-style:none;display:flex;justify-content:space-between;align-items:center; }
    details.bill-details summary::after { content:'▼';font-size:9px;color:#8aaa9e;transition:transform 0.2s; }
    details.bill-details[open] summary::after { transform:rotate(180deg); }
    details.bill-details[open] summary { margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid rgba(90,122,112,0.3); }
    .bill-item { display:flex;justify-content:space-between;align-items:flex-start;
                 padding:6px 0;border-bottom:1px dashed rgba(90,122,112,0.3);gap:10px; }
    .bill-item:last-child { border-bottom:none; }
    .bill-desc { font-size:12px;color:#e0d5c4;flex:1;min-width:0; }
    .bill-desc .paid-by { color:#8aaa9e;font-size:10px;display:block;margin-top:2px; }
    .bill-desc .split { color:#5a7a70;font-size:10px;display:block; }
    .bill-amount { font-size:13px;font-weight:700;color:#e8a84c;white-space:nowrap; }

    .cta { text-align:center;margin-top:24px;padding-top:16px;
           border-top:2px solid transparent;
           background-image:linear-gradient(#2d4a4a,#2d4a4a),linear-gradient(90deg,transparent,#e8a84c,#8aaa9e,#e8a84c,transparent);
           background-origin:padding-box,border-box;background-clip:padding-box,border-box; }
    .cta p { color:#5a7a70;font-size:12px;margin-bottom:10px; }
    .cta a { display:inline-block;background:linear-gradient(135deg,#e8a84c,#c88830);color:#1e3636;
             text-decoration:none;padding:10px 20px;border-radius:10px;font-weight:700;font-size:13px;
             box-shadow:4px 4px 8px rgba(10,30,30,0.4),-2px -2px 4px rgba(60,100,100,0.1); }
    .footer { text-align:center;margin-top:16px;font-size:10px;color:#5a7a70; }
    .footer a { color:#8aaa9e; }

    /* Identity modal */
    .modal-backdrop { position:fixed;inset:0;background:rgba(0,0,0,0.6);
      display:flex;align-items:center;justify-content:center;z-index:1000;padding:16px; }
    .modal { background:#2d4a4a;border:2px solid #e8a84c;border-radius:16px;padding:24px;
      max-width:340px;width:100%;display:flex;flex-direction:column;gap:10px;color:#e0d5c4;
      box-shadow:8px 8px 16px rgba(10,30,30,0.6); }
    .modal h3 { color:#e8a84c;margin:0 0 4px;font-size:18px; }
    .modal p { color:#8aaa9e;font-size:13px;margin:0 0 8px; }
    .modal button { padding:10px 14px;border:none;border-radius:10px;font-size:14px;
      font-weight:600;cursor:pointer;background:#1e3636;color:#e0d5c4;
      box-shadow:3px 3px 6px rgba(10,30,30,0.4);font-family:inherit; }
    .modal button:hover { background:#e8a84c;color:#1e3636; }
    .modal button.guest { background:transparent;border:1px solid #5a7a70;color:#8aaa9e; }
  </style>
</head>
<body>
  <div id="identity-modal" hidden></div>
  <div class="phone">
    <h1>{{share_title}}</h1>
    <div class="date">{{date}}</div>
    <div class="total-line">{{participants}} · Total: {{currency}} {{total}}</div>

    <div class="identity-card" id="identity-card" hidden>
      <div class="id-row">
        <span class="id-name" id="id-name"></span>
        <button class="id-edit" id="id-edit" hidden>{{edit_btn_label}}</button>
      </div>
      <div class="id-summary" id="id-summary"></div>
      <div class="acct-editor" id="acct-editor">
        <textarea id="acct-text" maxlength="500" rows="3"></textarea>
        <button id="acct-save">{{save_label}}</button>
        <span class="status" id="acct-status"></span>
      </div>
    </div>

    <div class="view-toggle" id="view-toggle" hidden>
      <button id="view-toggle-btn">{{view_all_label}}</button>
    </div>

    <hr class="divider">
    {{settlements_html}}
    <div class="summary">{{num_settlements}} transfer{{s_plural}} to settle <span class="check">✓</span></div>
    {{bill_details_html}}
    <div class="cta">
      <p>{{cta_q}}</p>
      <a href="/">{{cta_btn}}</a>
    </div>
    <div class="footer">Powered by Redarch</div>
  </div>
  <script>window.__SHARE = {{bootstrap_json}};</script>
  <script>
  (function() {
    var SHARE = window.__SHARE || {};
    var shareId = SHARE.share_id;
    var participants = SHARE.participants || [];
    var settlements = SHARE.settlements || [];
    var LBL = SHARE.labels || {};
    if (!shareId) return;

    var deviceId = localStorage.getItem('split_device_id');
    if (!deviceId) {
      deviceId = (crypto.randomUUID && crypto.randomUUID()) ||
                 (Date.now().toString(36) + Math.random().toString(36).slice(2));
      localStorage.setItem('split_device_id', deviceId);
    }

    var IDENTITY_KEY = 'split_identity:' + shareId;
    var identity = localStorage.getItem(IDENTITY_KEY);
    var accounts = {};
    var showAll = false;

    function esc(s) {
      return String(s).replace(/[&<>"']/g, function(c) {
        return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#x27;'}[c];
      });
    }

    var ZERO_DEC = {TWD:1,JPY:1,KRW:1,VND:1,IDR:1};
    function fmt(n) { var d=ZERO_DEC[SHARE.currency]?0:2; return Number(n).toLocaleString('en-US', {minimumFractionDigits:d, maximumFractionDigits:d}); }

    function totalsFor(name) {
      var owed = 0, owes = 0;
      settlements.forEach(function(s) {
        if (s.to === name) owed += Number(s.amount) || 0;
        if (s.from === name) owes += Number(s.amount) || 0;
      });
      return {owed: owed, owes: owes};
    }

    function isRealIdentity() {
      return identity && identity !== '__guest__';
    }

    function fetchAccounts() {
      return fetch('/v1/share/' + encodeURIComponent(shareId) + '/accounts')
        .then(function(r) { return r.ok ? r.json() : {}; })
        .then(function(d) { accounts = d || {}; })
        .catch(function() { accounts = {}; });
    }

    function showIdentityModal() {
      var modal = document.getElementById('identity-modal');
      if (!modal) return;
      var html = '<div class="modal-backdrop"><div class="modal">' +
                 '<h3>' + esc(LBL.modal_title || '') + '</h3>' +
                 '<p>' + esc(LBL.modal_body || '') + '</p>';
      participants.forEach(function(p) {
        html += '<button data-name="' + esc(p) + '">' + esc(p) + '</button>';
      });
      html += '<button class="guest" data-name="__guest__">' + esc(LBL.guest_label || '') + '</button>';
      html += '</div></div>';
      modal.innerHTML = html;
      modal.hidden = false;
      modal.querySelectorAll('button[data-name]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          identity = btn.getAttribute('data-name');
          localStorage.setItem(IDENTITY_KEY, identity);
          modal.hidden = true;
          modal.innerHTML = '';
          renderAll();
        });
      });
    }

    function renderIdentityCard() {
      var card = document.getElementById('identity-card');
      var nameEl = document.getElementById('id-name');
      var editBtn = document.getElementById('id-edit');
      var summary = document.getElementById('id-summary');
      var editor = document.getElementById('acct-editor');
      if (!card) return;

      if (!isRealIdentity()) {
        card.hidden = true;
        return;
      }
      card.hidden = false;
      nameEl.textContent = (LBL.greeting || '') + identity;

      var t = totalsFor(identity);
      var lines = '';
      if (t.owed > 0) {
        lines += '<div><span class="owed">' + esc(LBL.owed_label || '') + ' ' +
                 esc(SHARE.currency || '') + ' ' + esc(fmt(t.owed)) + '</span></div>';
      }
      if (t.owes > 0) {
        lines += '<div><span class="owes">' + esc(LBL.owes_label || '') + ' ' +
                 esc(SHARE.currency || '') + ' ' + esc(fmt(t.owes)) + '</span></div>';
      }
      summary.innerHTML = lines;

      editBtn.hidden = !(t.owed > 0);
      if (editBtn.hidden) {
        editor.classList.remove('open');
      } else {
        var ta = document.getElementById('acct-text');
        ta.value = accounts[identity] || '';
      }
    }

    function renderSettlements() {
      var rows = document.querySelectorAll('.settlement');
      rows.forEach(function(row) {
        var idx = parseInt(row.dataset.idx, 10);
        var s = settlements[idx];
        if (!s) return;
        var visible;
        if (!isRealIdentity() || showAll) {
          visible = true;
        } else {
          visible = (s && (s.from === identity || s.to === identity));
        }
        row.classList.toggle('hidden', !visible);

        var prior = row.querySelector('.payee-account');
        if (prior) prior.remove();
        if (!isRealIdentity() || !s || s.from !== identity) return;

        var acct = accounts[s.to];
        var div = document.createElement('div');
        div.className = 'payee-account';
        if (acct) {
          var code = document.createElement('code');
          code.textContent = acct;
          var btn = document.createElement('button');
          btn.className = 'copy-btn';
          btn.textContent = LBL.copy_label || 'Copy';
          btn.addEventListener('click', function() {
            if (navigator.clipboard) navigator.clipboard.writeText(acct);
            btn.textContent = LBL.copied_label || 'Copied';
            setTimeout(function() { btn.textContent = LBL.copy_label || 'Copy'; }, 1500);
          });
          div.appendChild(code);
          div.appendChild(btn);
        } else {
          var span = document.createElement('span');
          span.className = 'muted';
          span.textContent = s.to + ' ' + (LBL.no_account_suffix || '');
          div.appendChild(span);
        }
        row.appendChild(div);
      });

      var toggleWrap = document.getElementById('view-toggle');
      var toggleBtn = document.getElementById('view-toggle-btn');
      if (isRealIdentity()) {
        toggleWrap.hidden = false;
        toggleBtn.textContent = showAll ? (LBL.view_mine_label || '') : (LBL.view_all_label || '');
      } else {
        toggleWrap.hidden = true;
      }
    }

    function renderAll() {
      renderIdentityCard();
      renderSettlements();
    }

    document.getElementById('id-edit').addEventListener('click', function() {
      document.getElementById('acct-editor').classList.toggle('open');
    });
    document.getElementById('acct-save').addEventListener('click', function() {
      if (!isRealIdentity()) return;
      var ta = document.getElementById('acct-text');
      var status = document.getElementById('acct-status');
      var text = ta.value;
      status.textContent = LBL.saving_label || '';
      fetch('/v1/share/' + encodeURIComponent(shareId) + '/accounts/' +
            encodeURIComponent(identity), {
        method: 'PUT',
        headers: {'Content-Type': 'application/json', 'x-device-id': deviceId},
        body: JSON.stringify({account_text: text}),
      }).then(function(r) {
        if (r.ok) {
          accounts[identity] = text;
          status.textContent = LBL.saved_label || '';
          renderSettlements();
        } else {
          status.textContent = LBL.save_failed_label || '';
        }
      }).catch(function() { status.textContent = LBL.save_failed_label || ''; });
    });
    document.getElementById('view-toggle-btn').addEventListener('click', function() {
      showAll = !showAll;
      renderSettlements();
    });

    fetchAccounts().then(function() {
      if (!identity) {
        showIdentityModal();
      } else {
        renderAll();
      }
    });
  })();
  </script>
</body>
</html>"""


def _esc(s: str) -> str:
    """HTML-escape user input to prevent XSS."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;")


def _render_share_page(result: dict, created_at: str = "", si: dict = None,
                       share_id: str = "", request_body: dict = None) -> str:
    """Render the share page HTML from a split result."""
    currency_raw = result.get("currency", "")
    currency = _esc(currency_raw)
    total = result.get("total_expenses", 0)
    settlements = result.get("settlements", [])
    summary = result.get("summary", [])
    names = [_esc(s["participant"]) for s in summary]
    n_sett = len(settlements)

    si = si or {}
    labels = {
        "greeting": si.get("greeting", "嗨，"),
        "owed_label": si.get("owed", "別人欠你"),
        "owes_label": si.get("owes", "你要付"),
        "view_all_label": si.get("view_all", "顯示全部 ▼"),
        "view_mine_label": si.get("view_mine", "只看自己 ▲"),
        "copy_label": si.get("copy", "複製"),
        "copied_label": si.get("copied", "已複製"),
        "no_account_suffix": si.get("no_account", "還沒提供帳號"),
        "saving_label": si.get("saving", "儲存中…"),
        "saved_label": si.get("saved", "已儲存 ✓"),
        "save_failed_label": si.get("save_failed", "儲存失敗"),
        "modal_title": si.get("modal_title", "你是哪一位？"),
        "modal_body": si.get("modal_body", "選擇身分後，需要付錢給你的人才會看到你的帳號。"),
        "guest_label": si.get("guest", "我只是路人"),
        "bill_title": si.get("bill_title", "帳單明細"),
        "paid_by_suffix": si.get("paid_by_suffix", "付"),
        "split_prefix": si.get("split_prefix", "分給"),
    }

    # Build bill details HTML from request_body expenses
    expenses = (request_body or {}).get("expenses", [])
    n_expenses = len(expenses)
    if n_expenses > 0:
        bill_items_html = ""
        for exp in expenses:
            desc = _esc(exp.get("description", ""))
            paid_by = _esc(exp.get("paid_by", ""))
            amount = exp.get("amount", 0)
            split_among = [_esc(p) for p in exp.get("split_among", [])]
            bill_items_html += (
                f'<div class="bill-item">'
                f'<div class="bill-desc">{desc}'
                f'<span class="paid-by">{paid_by} {_esc(labels["paid_by_suffix"])}</span>'
                f'<span class="split">{_esc(labels["split_prefix"])} {", ".join(split_among)}</span>'
                f'</div>'
                f'<div class="bill-amount">{currency} {_format_amount(currency_raw, amount)}</div>'
                f'</div>'
            )
        bill_details_html = (
            f'<details class="bill-details">'
            f'<summary>{_esc(labels["bill_title"])}（{n_expenses} 筆）· {currency} {_format_amount(currency_raw, total)}</summary>'
            f'{bill_items_html}'
            f'</details>'
        )
    else:
        bill_details_html = ""

    bootstrap = {
        "share_id": share_id,
        "currency": result.get("currency", ""),
        "participants": [s["participant"] for s in summary],
        "settlements": [
            {"from": s["from"], "to": s["to"], "amount": s["amount"]}
            for s in settlements
        ],
        "labels": labels,
    }
    # Defensive escapes for embedding inside <script>: prevent </script> close
    # (\u003c), unicode line terminators that break JS string literals, and
    # single-quote/ampersand sequences that might be flagged by XSS heuristics
    # even though they're safe inside a JSON string literal.
    bootstrap_json = (json.dumps(bootstrap)
                      .replace("<", "\\u003c")
                      .replace("&", "\\u0026")
                      .replace("'", "\\u0027")
                      .replace("\u2028", "\\u2028")
                      .replace("\u2029", "\\u2029"))

    settlements_html = ""
    for i, s in enumerate(settlements):
        settlements_html += (
            f'<div class="settlement" data-idx="{i}" style="--i:{i}">'
            f'<div class="sett-main">'
            f'<span><span class="from">{_esc(s["from"])}</span> → '
            f'<span class="to">{_esc(s["to"])}</span></span>'
            f'<span class="amount">{currency} {_format_amount(currency_raw, s["amount"])}</span>'
            f'</div>'
            f'</div>'
        )

    s_plural = "s" if n_sett != 1 else ""
    replacements = {
        "{{title}}": _esc(f"{currency_raw} {total:,.0f} split"),
        "{{og_title}}": _esc(f"Split: {currency_raw} {total:,.0f} between {len(names)} people"),
        "{{og_desc}}": _esc(f"{n_sett} transfer{s_plural} needed to settle"),
        "{{date}}": _esc(created_at[:10]) if created_at else "",
        "{{participants}}": ", ".join(names),
        "{{currency}}": currency,
        "{{total}}": _format_amount(currency_raw, total),
        "{{settlements_html}}": settlements_html,
        "{{num_settlements}}": str(n_sett),
        "{{s_plural}}": s_plural,
        "{{share_title}}": _esc(si.get("title", "Split Senpai")),
        "{{cta_q}}": _esc(si.get("cta_q", "Need to split a bill?")),
        "{{cta_btn}}": _esc(si.get("cta", "Start splitting →")),
        "{{edit_btn_label}}": _esc("分享轉帳帳號 ✏️"),
        "{{save_label}}": _esc("儲存"),
        "{{view_all_label}}": _esc(labels["view_all_label"]),
        "{{bill_details_html}}": bill_details_html,
        "{{bootstrap_json}}": bootstrap_json,
    }
    html = SHARE_PAGE_TEMPLATE
    for key, value in replacements.items():
        html = html.replace(key, value)
    return html


# SECURITY: baseline response headers applied to HTML responses. CSP is the
# last-line defense against XSS even if an escape-hatch slips in. unsafe-inline
# is kept only because the shared-page JS is inlined; long-term, move to an
# external script and drop it.
_HTML_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' https://esm.sh https://unpkg.com 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://api.qrserver.com; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


def _html_response(status: int, body: str) -> dict:
    headers = {"Content-Type": "text/html; charset=utf-8"}
    headers.update(_HTML_SECURITY_HEADERS)
    return {"statusCode": status, "headers": headers, "body": body}


_METHOD_NOT_ALLOWED = {
    "statusCode": 405,
    "headers": {"Content-Type": "application/json"},
    "body": json.dumps({"error": "Method Not Allowed"}),
}

_ROUTE_METHODS = {
    "/openapi.json": "GET",
    "/health": "GET",
    "/docs": "GET",
    "/": "GET",
    "/v1/share": "POST",
    "/v1/split_settle": "POST",
    "/v1/groups": "POST",
    "/.well-known/apple-app-site-association": "GET",
    "/.well-known/assetlinks.json": "GET",
}

# App Store IDs — populated via environment variables after app store registration.
# If unset, the /.well-known/* endpoints return 404 so we never publish placeholder
# fingerprints that could be claimed by a malicious app.
_APPLE_APP_ID = os.environ.get("APPLE_APP_ID", "")
_ANDROID_PACKAGE = os.environ.get("ANDROID_PACKAGE", "")
_ANDROID_SHA256 = os.environ.get("ANDROID_SHA256", "")


def _allowed_hosts() -> set:
    """Set of Host headers permitted to reach this Lambda. Set ALLOWED_HOSTS
    env var in production to the custom domain (comma-separated if multiple)
    to block direct hits on the raw execute-api.amazonaws.com URL."""
    raw = os.environ.get("ALLOWED_HOSTS", "")
    if not raw:
        return set()
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def lambda_handler(event, context):
    path = event.get("rawPath", "")
    method = event.get("requestContext", {}).get("http", {}).get("method", "")

    # SECURITY: reject direct hits on the default execute-api endpoint.
    # HTTP API has no disableExecuteApiEndpoint flag, so we enforce host
    # allow-listing in-app. Traffic via the custom domain carries the
    # correct Host header; direct curl to *.amazonaws.com does not.
    allowed = _allowed_hosts()
    if allowed:
        hdrs = event.get("headers") or {}
        # Prefer X-Forwarded-Host when present (set by Cloudflare / most CDNs
        # to the original client-facing hostname) and fall back to Host.
        xfh = (hdrs.get("x-forwarded-host") or "").lower()
        host_hdr = (hdrs.get("host") or "").lower()
        host_only = (xfh or host_hdr).split(":", 1)[0]
        if host_only and host_only not in allowed:
            return {
                "statusCode": 403,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "Forbidden"}),
            }

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
        return _html_response(200, SWAGGER_HTML)

    if path == "/":
        return _html_response(200, APP_HTML)

    if path == "/.well-known/apple-app-site-association":
        if not _APPLE_APP_ID:
            return {"statusCode": 404, "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"error": "not configured"})}
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({
                "applinks": {
                    "apps": [],
                    "details": [{"appID": _APPLE_APP_ID, "paths": ["/s/*"]}],
                },
            }),
        }

    if path == "/.well-known/assetlinks.json":
        if not _ANDROID_PACKAGE or not _ANDROID_SHA256:
            return {"statusCode": 404, "headers": {"Content-Type": "application/json"},
                    "body": json.dumps({"error": "not configured"})}
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps([{
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": _ANDROID_PACKAGE,
                    "sha256_cert_fingerprints": [_ANDROID_SHA256],
                },
            }]),
        }

    if path == "/admin" or path.startswith("/admin/"):
        try:
            claims, err = _check_admin_auth(event)
            if err:
                return err
            return _handle_admin(event, claims)
        except Exception:
            logger.exception("admin handler crashed")
            return {
                "statusCode": 500,
                "headers": SECURE_JSON_HEADERS,
                "body": json.dumps({"error": "internal server error"}),
            }

    _share_parts = path.split("/")
    # ['', 'v1', 'share', '<id>', 'accounts', ...]
    if (len(_share_parts) >= 5 and _share_parts[1] == "v1"
            and _share_parts[2] == "share" and _share_parts[4] == "accounts"):
        return _handle_share_accounts(event)

    if path.startswith("/v1/share/"):
        return _handle_share_json(event)

    if path.startswith("/s/"):
        return _handle_share_page(event)

    if path == "/v1/share":
        return _handle_share(event)

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


def _handle_share_json(event):
    """GET /v1/share/{id} — return share data as JSON for the native app."""
    path = event.get("rawPath", "")
    share_id = path.split("/v1/share/", 1)[-1].split("?")[0] if "/v1/share/" in path else ""
    if not share_id:
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "share_id required"}),
        }

    data = _get_share(share_id)
    if not data or data["ttl_expiry"] < time.time():
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Share not found or expired"}),
        }

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "share_id": share_id,
            "request_body": data["request_body"],
            "result": data["result"],
            "created_at": data["created_at"],
        }),
    }


_SHARE_MAX_BODY_BYTES = 64 * 1024  # 64 KB hard cap on anonymous share payloads


def _handle_share(event):
    """POST /v1/share — public endpoint, saves split result, returns share link."""
    try:
        raw = event.get("body") or "{}"
        if len(raw) > _SHARE_MAX_BODY_BYTES:
            return {
                "statusCode": 413,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"error": "payload too large"}),
            }
        body = json.loads(raw)
        lang = body.pop("lang", "en")
        result = split_settle(body)
        share_id = _generate_share_id()
        _save_share(share_id, body, result)
        url = f"/s/{share_id}" + (f"?lang={lang}" if lang != "en" else "")
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"share_id": share_id, "url": url}),
        }
    except ValueError as e:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": str(e)}),
        }
    except Exception:
        logger.exception("Unhandled error in _handle_share")
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Internal server error"}),
        }


_SHARE_I18N = {
    "en": {"title": "Split Senpai", "iam": "I am...", "all": "All", "cta_q": "Need to split a bill?", "cta": "Start splitting →"},
    "zh-TW": {"title": "分帳仙貝", "iam": "我是...", "all": "全部", "cta_q": "也要分帳？", "cta": "開始分帳 →"},
    "ja": {"title": "割り勘先輩", "iam": "私は...", "all": "全部", "cta_q": "割り勘する？", "cta": "始める →"},
}


def _handle_share_page(event):
    """GET /s/{id} — render shared result page."""
    path = event.get("rawPath", "")
    share_id = path.split("/s/", 1)[-1].split("?")[0] if "/s/" in path else ""
    if not share_id:
        return _html_response(404, NOT_FOUND_HTML)

    data = _get_share(share_id)
    if not data or data["ttl_expiry"] < time.time():
        return _html_response(404, NOT_FOUND_HTML)

    qs = event.get("queryStringParameters") or {}
    lang = qs.get("lang", "en")
    si = _SHARE_I18N.get(lang, _SHARE_I18N["en"])

    html_out = _render_share_page(data["result"], data["created_at"], si,
                                  share_id=share_id,
                                  request_body=data.get("request_body"))
    return _html_response(200, html_out)


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
    if len(set(participants)) != len(participants):
        raise ValueError("duplicate participant names not allowed")
    for p in participants:
        if len(p) > 50:
            raise ValueError(f"participant name too long: max 50 chars")
    if len(expenses) < 1:
        raise ValueError("at least 1 expense required")
    if len(expenses) > 200:
        raise ValueError("expenses cannot exceed 200")

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

        # Only add execution block if ALL participants have wallets
        all_have_wallets = all(wallet_map.get(s[role]) for s in settlements for role in ("from", "to"))
        if all_have_wallets:
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
# ============================================================
# Admin dashboard — ported from feat/dev-1/issue-26
# ============================================================

SECURE_JSON_HEADERS = {
    "Content-Type": "application/json",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

SECURE_HTML_HEADERS = {
    "Content-Type": "text/html; charset=utf-8",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}

_SHARE_ID_RE = re.compile(r'^[A-Za-z0-9_-]{6,32}$')


def _sanitize_log(value) -> str:
    """Strip control chars from log values to prevent log injection."""
    return re.sub(r'[\r\n\t\x00-\x1f]', ' ', str(value))[:200]


def _scan_all_shares() -> list:
    """Scan GroupsTable for all SHARE# items. Returns list of raw DynamoDB items (capped at 1000)."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        return []
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    items = []
    last_key = None
    MAX_ITEMS = 1000
    while True:
        kwargs = {
            "TableName": table,
            "FilterExpression": "begins_with(PK, :prefix)",
            "ExpressionAttributeValues": {":prefix": {"S": "SHARE#"}},
            "Limit": 100,
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = client.scan(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key or len(items) >= MAX_ITEMS:
            break
    return items[:MAX_ITEMS]


def _delete_share(share_id: str) -> None:
    """Delete a share from DynamoDB by id."""
    import boto3
    table = os.environ.get("GROUPS_TABLE", "")
    if not table:
        raise ValueError("GROUPS_TABLE not configured")
    client = boto3.client("dynamodb", region_name="ap-northeast-1")
    client.delete_item(
        TableName=table,
        Key={"PK": {"S": f"SHARE#{share_id}"}, "SK": {"S": "RESULT"}},
    )
    logger.info(f"Admin deleted share: {_sanitize_log(share_id)}")



CURRENCY_DECIMALS = {
    "TWD": 0, "JPY": 0, "KRW": 0, "VND": 0, "IDR": 0,
    "USD": 2, "EUR": 2, "GBP": 2, "AUD": 2, "CAD": 2,
    "SGD": 2, "HKD": 2, "CNY": 2, "THB": 2, "MYR": 2, "PHP": 2, "INR": 2,
}


def _format_amount(currency: str, amount: float) -> str:
    """Format amount with currency-aware decimal places."""
    decimals = CURRENCY_DECIMALS.get(currency, 2)
    return f"{amount:,.{decimals}f}"


# ---------- Cloudflare Access JWT verification ----------

_jwks_cache: dict = {}


def _b64url_decode(data: str) -> bytes:
    """Base64-url decode with padding."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


def _fetch_jwks(team_domain: str) -> dict:
    """Fetch and cache JWKS from Cloudflare Access. 1h cache."""
    global _jwks_cache
    now = time.time()
    if team_domain in _jwks_cache and _jwks_cache[team_domain]["expires"] > now:
        return _jwks_cache[team_domain]["jwks"]
    url = f"https://{team_domain}/cdn-cgi/access/certs"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            jwks = json.loads(resp.read())
        _jwks_cache[team_domain] = {"jwks": jwks, "expires": now + 3600}
        return jwks
    except Exception as e:
        logger.error(f"Failed to fetch JWKS: {e}")
        return {"keys": []}


def _verify_access_jwt(token: str):
    """Verify CF Access JWT (RS256). Returns claims dict or None."""
    team_domain = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip()
    expected_aud = os.environ.get("CF_ACCESS_AUD", "").strip()
    if not team_domain or not expected_aud:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
        signature = _b64url_decode(parts[2])
    except Exception:
        return None
    kid = header.get("kid")
    if not kid:
        return None
    jwks = _fetch_jwks(team_domain)
    key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not key_data:
        return None
    try:
        from Crypto.PublicKey import RSA
        from Crypto.Signature import pkcs1_15
        from Crypto.Hash import SHA256
        n = int.from_bytes(_b64url_decode(key_data["n"]), "big")
        e = int.from_bytes(_b64url_decode(key_data["e"]), "big")
        rsa_key = RSA.construct((n, e))
        signing_input = f"{parts[0]}.{parts[1]}".encode()
        h = SHA256.new(signing_input)
        pkcs1_15.new(rsa_key).verify(h, signature)
    except Exception as e:
        logger.error(f"JWT verify failed: {e}")
        return None
    expected_iss = f"https://{team_domain}"
    if payload.get("iss") != expected_iss:
        return None
    aud = payload.get("aud")
    if isinstance(aud, list):
        if expected_aud not in aud:
            return None
    elif aud != expected_aud:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def _admin_unauthorized(status: int, reason: str):
    return {
        "statusCode": status,
        "headers": SECURE_JSON_HEADERS,
        "body": json.dumps({"error": reason}),
    }


def _check_admin_auth(event: dict):
    """
    Verify admin authentication. Returns (claims_dict, None) on success
    or (None, error_response) on failure.
    """
    if not os.environ.get("CF_ACCESS_TEAM_DOMAIN", "").strip():
        return None, _admin_unauthorized(503, "admin not configured")
    headers = event.get("headers") or {}
    jwt = (
        headers.get("cf-access-jwt-assertion")
        or headers.get("Cf-Access-Jwt-Assertion")
        or headers.get("CF-Access-Jwt-Assertion")
    )
    if not jwt:
        return None, _admin_unauthorized(401, "missing access token")
    claims = _verify_access_jwt(jwt)
    if not claims:
        return None, _admin_unauthorized(401, "invalid access token")
    allowed_email = os.environ.get("CF_ALLOWED_EMAIL", "").strip().lower()
    user_email = (claims.get("email") or "").strip().lower()
    if allowed_email and user_email != allowed_email:
        logger.warning(f"Admin access denied for email: {_sanitize_log(user_email)}")
        return None, _admin_unauthorized(403, "forbidden")
    return claims, None



def _handle_admin(event: dict, claims: dict) -> dict:
    """Route /admin/* requests."""
    path = event.get("rawPath", "")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")

    if path == "/admin" or path == "/admin/":
        return _admin_render_dashboard()

    if path == "/admin/api/stats" and method == "GET":
        return _admin_stats()

    if path == "/admin/api/cloudflare/analytics" and method == "GET":
        return _admin_cf_analytics()

    if path == "/admin/api/shares" and method == "GET":
        return _admin_list_shares()

    if path.startswith("/admin/api/shares/"):
        share_id = path.split("/admin/api/shares/", 1)[1]
        if not _SHARE_ID_RE.match(share_id):
            return {
                "statusCode": 400,
                "headers": SECURE_JSON_HEADERS,
                "body": json.dumps({"error": "invalid share id"}),
            }
        if method == "GET":
            return _admin_get_share(share_id)
        if method == "DELETE":
            # CSRF: verify Origin header
            headers = event.get("headers") or {}
            origin = headers.get("origin") or headers.get("Origin") or ""
            if origin not in ("https://split-admin.redarch.dev", "https://split.redarch.dev"):
                return {
                    "statusCode": 403,
                    "headers": SECURE_JSON_HEADERS,
                    "body": json.dumps({"error": "cross-origin request blocked"}),
                }
            return _admin_delete_share(share_id)

    return {
        "statusCode": 404,
        "headers": SECURE_JSON_HEADERS,
        "body": json.dumps({"error": "not found"}),
    }



_ADMIN_SPA_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex,nofollow">
<title>Split Senpai Admin</title>
<style>
  :root {
    --bg: #2d4a4a;
    --layer1: #1e3636;
    --layer2: #162a2a;
    --accent: #e8a84c;
    --text: #e0d5c4;
    --muted: #a0c4b8;
    --border: #3a5e5e;
    --error: #e06050;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    font-size: 14px;
    line-height: 1.5;
  }
  .container { max-width: 1100px; margin: 0 auto; padding: 24px 16px; }
  h1 { color: var(--accent); font-size: 24px; margin: 0 0 24px; }
  h2 { color: var(--accent); font-size: 16px; margin: 24px 0 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
  .card {
    background: var(--layer1);
    border-radius: 12px;
    padding: 16px;
    border: 1px solid var(--border);
  }
  .card .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  .card .value { color: var(--accent); font-size: 24px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }
  table { width: 100%; border-collapse: collapse; background: var(--layer1); border-radius: 12px; overflow: hidden; }
  th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); }
  th { background: var(--layer2); color: var(--muted); font-size: 11px; text-transform: uppercase; }
  td { font-size: 13px; }
  td.amount { color: var(--accent); font-weight: 600; font-variant-numeric: tabular-nums; }
  button { background: var(--accent); color: var(--layer1); border: none; padding: 6px 12px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 12px; }
  button:hover { opacity: 0.85; }
  button.danger { background: var(--error); color: white; }
  button.outline { background: transparent; color: var(--accent); border: 1px solid var(--accent); }
  .chart-container { background: var(--layer1); border-radius: 12px; padding: 16px; }
  .empty { color: var(--muted); text-align: center; padding: 32px; font-style: italic; }
  .loading { color: var(--muted); padding: 16px; text-align: center; }
  .error { color: var(--error); padding: 16px; }
  code { background: var(--layer2); padding: 2px 6px; border-radius: 4px; font-size: 11px; }
</style>
</head>
<body>
<div id="app" class="container">
  <h1>分帳仙貝 Admin</h1>
  <div id="content"></div>
</div>
<script type="module">
import { h, render } from 'https://esm.sh/preact@10.19.0';
import { useState, useEffect } from 'https://esm.sh/preact@10.19.0/hooks';
import htm from 'https://esm.sh/htm@3.1.1';
const html = htm.bind(h);

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(path + ' returned ' + r.status);
  return r.json();
}

function StatCard({ label, value }) {
  return html`<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`;
}

function LineChart({ data }) {
  if (!data || data.length === 0) return html`<div class="empty">No data</div>`;
  const w = 600, hgt = 180, pad = 30;
  const max = Math.max(...data.map(d => d.count), 1);
  const points = data.map((d, i) => {
    const x = pad + (i / Math.max(data.length - 1, 1)) * (w - 2 * pad);
    const y = hgt - pad - (d.count / max) * (hgt - 2 * pad);
    return `${x},${y}`;
  }).join(' ');
  return html`
    <svg viewBox="0 0 ${w} ${hgt}" style="width:100%;height:auto">
      <polyline fill="none" stroke="#e8a84c" stroke-width="2" points=${points} />
      ${data.map((d, i) => {
        const x = pad + (i / Math.max(data.length - 1, 1)) * (w - 2 * pad);
        const y = hgt - pad - (d.count / max) * (hgt - 2 * pad);
        return html`<circle cx=${x} cy=${y} r="3" fill="#e8a84c" />`;
      })}
      <text x=${pad} y=${hgt - 8} fill="#a0c4b8" font-size="10">${data[0].date}</text>
      <text x=${w - pad} y=${hgt - 8} fill="#a0c4b8" font-size="10" text-anchor="end">${data[data.length - 1].date}</text>
      <text x="8" y="20" fill="#a0c4b8" font-size="10">${max}</text>
    </svg>
  `;
}

function PieChart({ data }) {
  const entries = Object.entries(data || {});
  if (entries.length === 0) return html`<div class="empty">No data</div>`;
  const total = entries.reduce((s, [, v]) => s + v, 0);
  const colors = ['#e8a84c', '#7aa0d0', '#3a5a9a', '#b0c8e8', '#e06050'];
  let angle = -Math.PI / 2;
  const cx = 100, cy = 100, r = 80;
  const slices = entries.map(([key, val], i) => {
    const sliceAngle = (val / total) * 2 * Math.PI;
    const x1 = cx + r * Math.cos(angle);
    const y1 = cy + r * Math.sin(angle);
    angle += sliceAngle;
    const x2 = cx + r * Math.cos(angle);
    const y2 = cy + r * Math.sin(angle);
    const large = sliceAngle > Math.PI ? 1 : 0;
    const path = `M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${large} 1 ${x2},${y2} Z`;
    return { path, color: colors[i % colors.length], key, val };
  });
  return html`
    <div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap">
      <svg viewBox="0 0 200 200" style="width:200px;height:200px">
        ${slices.map(s => html`<path d=${s.path} fill=${s.color} />`)}
      </svg>
      <div>
        ${slices.map(s => html`
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
            <div style="width:12px;height:12px;background:${s.color};border-radius:2px"></div>
            <span style="color:#e0d5c4">${s.key}: ${s.val}</span>
          </div>
        `)}
      </div>
    </div>
  `;
}

function ShareList({ items, onDelete }) {
  if (!items || items.length === 0) return html`<div class="empty">No shares</div>`;
  return html`
    <table>
      <thead>
        <tr><th>ID</th><th>Date</th><th>Currency</th><th>Total</th><th>People</th><th></th></tr>
      </thead>
      <tbody>
        ${items.map(item => html`
          <tr>
            <td><code>${item.share_id}</code></td>
            <td>${(item.created_at || '').slice(0, 16).replace('T', ' ')}</td>
            <td>${item.currency}</td>
            <td class="amount">${(({'TWD':1,'JPY':1,'KRW':1,'VND':1,'IDR':1})[item.currency]?item.total.toLocaleString(undefined,{maximumFractionDigits:0}):item.total.toLocaleString())}</td>
            <td>${item.participants_preview}</td>
            <td>
              <button class="outline" onClick=${() => window.open('https://split.redarch.dev/s/' + item.share_id, '_blank')}>View</button>
              ${' '}
              <button class="danger" onClick=${() => {
                if (confirm('Delete share ' + item.share_id + '?')) onDelete(item.share_id);
              }}>Delete</button>
            </td>
          </tr>
        `)}
      </tbody>
    </table>
  `;
}

function App() {
  const [stats, setStats] = useState(null);
  const [shares, setShares] = useState(null);
  const [cf, setCf] = useState(null);
  const [error, setError] = useState(null);

  async function loadAll() {
    try {
      const [s, sh, c] = await Promise.all([
        api('/api/stats'),
        api('/api/shares'),
        api('/api/cloudflare/analytics').catch(() => ({ requests_24h: 0, blocked_24h: 0 })),
      ]);
      setStats(s);
      setShares(sh.items);
      setCf(c);
    } catch (e) {
      setError(e.message);
    }
  }

  async function deleteShare(id) {
    try {
      await fetch('/api/shares/' + id, { method: 'DELETE' });
      loadAll();
    } catch (e) {
      alert('Delete failed: ' + e.message);
    }
  }

  useEffect(() => { loadAll(); }, []);

  if (error) return html`<div class="error">Error: ${error}</div>`;
  if (!stats || !shares) return html`<div class="loading">Loading...</div>`;

  return html`
    <div>
      <h2>📊 Stats</h2>
      <div class="grid">
        <${StatCard} label="Total Shares" value=${stats.total_shares} />
        <${StatCard} label="CF Requests 24h" value=${cf?.requests_24h ?? '-'} />
        <${StatCard} label="CF Blocked 24h" value=${cf?.blocked_24h ?? '-'} />
        <${StatCard} label="Currencies" value=${Object.keys(stats.currency_breakdown).length} />
      </div>

      <h2>📈 Shares per Day</h2>
      <div class="chart-container">
        <${LineChart} data=${stats.shares_by_day} />
      </div>

      <h2>🥧 Currency Breakdown</h2>
      <div class="chart-container">
        <${PieChart} data=${stats.currency_breakdown} />
      </div>

      <h2>📋 Shares</h2>
      <${ShareList} items=${shares} onDelete=${deleteShare} />
    </div>
  `;
}

render(h(App), document.getElementById('content'));
</script>
</body>
</html>
"""




def _admin_render_dashboard() -> dict:
    return {
        "statusCode": 200,
        "headers": SECURE_HTML_HEADERS,
        "body": _ADMIN_SPA_HTML,
    }


def _admin_list_shares() -> dict:
    items = _scan_all_shares()
    out = []
    for item in items:
        try:
            pk = item.get("PK", {}).get("S", "")
            share_id = pk.replace("SHARE#", "")
            request_body = json.loads(item.get("request_body", {}).get("S", "{}"))
            result = json.loads(item.get("result", {}).get("S", "{}"))
            participants = request_body.get("participants", [])
            preview = ", ".join(participants[:3])
            if len(participants) > 3:
                preview += f" +{len(participants) - 3}"
            out.append({
                "share_id": share_id,
                "created_at": item.get("created_at", {}).get("S", ""),
                "currency": result.get("currency", "?"),
                "total": result.get("total_expenses", 0),
                "participants_count": len(participants),
                "participants_preview": preview,
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["created_at"], reverse=True)
    return {
        "statusCode": 200,
        "headers": SECURE_JSON_HEADERS,
        "body": json.dumps({"items": out}),
    }


def _admin_get_share(share_id: str) -> dict:
    data = _get_share(share_id)
    if not data:
        return {
            "statusCode": 404,
            "headers": SECURE_JSON_HEADERS,
            "body": json.dumps({"error": "not found"}),
        }
    return {
        "statusCode": 200,
        "headers": SECURE_JSON_HEADERS,
        "body": json.dumps(data),
    }


def _admin_delete_share(share_id: str) -> dict:
    try:
        _delete_share(share_id)
        return {
            "statusCode": 200,
            "headers": SECURE_JSON_HEADERS,
            "body": json.dumps({"deleted": True}),
        }
    except Exception as e:
        logger.error(f"Failed to delete share {share_id}: {e}")
        return {
            "statusCode": 500,
            "headers": SECURE_JSON_HEADERS,
            "body": json.dumps({"error": "delete failed"}),
        }


def _get_cf_api_token() -> str:
    """Read Cloudflare API token from Secrets Manager."""
    arn = os.environ.get("CF_API_TOKEN_ARN", "").strip()
    if not arn:
        return ""
    if arn in _secret_cache:
        return _secret_cache[arn]
    import boto3
    client = boto3.client("secretsmanager", region_name="ap-northeast-1")
    try:
        resp = client.get_secret_value(SecretId=arn)
        token = resp["SecretString"].strip()
        _secret_cache[arn] = token
        return token
    except Exception as e:
        logger.error(f"Failed to fetch CF token: {e}")
        return ""


def _cf_graphql_query(url: str, token: str, query: str, variables: dict = None) -> dict:
    """Execute a GraphQL query against the Cloudflare Analytics API."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _admin_cf_analytics() -> dict:
    arn = os.environ.get("CF_API_TOKEN_ARN", "").strip()
    zone_id = os.environ.get("CF_ZONE_ID", "").strip()
    if not arn or not zone_id:
        return {
            "statusCode": 503,
            "headers": SECURE_JSON_HEADERS,
            "body": json.dumps({"error": "cloudflare analytics not configured"}),
        }
    token = _get_cf_api_token()
    if not token:
        return {
            "statusCode": 503,
            "headers": SECURE_JSON_HEADERS,
            "body": json.dumps({"error": "cloudflare token unavailable"}),
        }
    today = time.strftime("%Y-%m-%d", time.gmtime())
    query = """
query($zoneTag: String!, $date: String!) {
  viewer {
    zones(filter: { zoneTag: $zoneTag }) {
      httpRequests1dGroups(
        limit: 1,
        filter: { date: $date },
        orderBy: [date_DESC]
      ) {
        sum { requests threats }
        dimensions { date }
      }
    }
  }
}
"""
    variables = {"zoneTag": zone_id, "date": today}
    try:
        resp = _cf_graphql_query("https://api.cloudflare.com/client/v4/graphql", token, query, variables)
        zones = resp.get("data", {}).get("viewer", {}).get("zones", [])
        if not zones or not zones[0].get("httpRequests1dGroups"):
            return {
                "statusCode": 200,
                "headers": SECURE_JSON_HEADERS,
                "body": json.dumps({"requests_24h": 0, "blocked_24h": 0}),
            }
        group = zones[0]["httpRequests1dGroups"][0]
        return {
            "statusCode": 200,
            "headers": SECURE_JSON_HEADERS,
            "body": json.dumps({
                "requests_24h": group["sum"]["requests"],
                "blocked_24h": group["sum"]["threats"],
            }),
        }
    except Exception as e:
        logger.error(f"CF analytics query failed: {e}")
        return {
            "statusCode": 500,
            "headers": SECURE_JSON_HEADERS,
            "body": json.dumps({"error": "analytics fetch failed"}),
        }


def _admin_stats() -> dict:
    """Aggregate share stats from DynamoDB."""
    items = _scan_all_shares()
    total = len(items)
    currency_count = {}
    currency_total = {}
    day_count = {}
    for item in items:
        try:
            result = json.loads(item.get("result", {}).get("S", "{}"))
            currency = result.get("currency", "?")
            amount = float(result.get("total_expenses", 0))
            currency_count[currency] = currency_count.get(currency, 0) + 1
            currency_total[currency] = currency_total.get(currency, 0) + amount
            created = item.get("created_at", {}).get("S", "")
            day = created[:10] if created else "?"
            day_count[day] = day_count.get(day, 0) + 1
        except Exception:
            continue
    avg_by_currency = {
        c: round(currency_total[c] / currency_count[c], 2)
        for c in currency_count
    }
    shares_by_day = sorted(
        [{"date": d, "count": c} for d, c in day_count.items()],
        key=lambda x: x["date"],
    )
    return {
        "statusCode": 200,
        "headers": SECURE_JSON_HEADERS,
        "body": json.dumps({
            "total_shares": total,
            "currency_breakdown": currency_count,
            "avg_amount_by_currency": avg_by_currency,
            "shares_by_day": shares_by_day,
        }),
    }
