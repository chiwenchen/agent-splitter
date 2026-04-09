import json
import sys
import os
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/split_settle"))

import handler
from handler import split_settle, lambda_handler


@pytest.fixture(autouse=True)
def reset_caches(monkeypatch):
    handler._secret_cache.clear()
    yield
    handler._secret_cache.clear()


# ---------------------------------------------------------------------------
# Core logic tests
# ---------------------------------------------------------------------------

def test_spec_example():
    result = split_settle({
        "currency": "TWD",
        "participants": ["Alice", "Bob", "Carol"],
        "expenses": [
            {"description": "晚餐", "paid_by": "Alice", "amount": 1200, "split_among": ["Alice", "Bob", "Carol"]},
            {"description": "計程車", "paid_by": "Bob", "amount": 300, "split_among": ["Alice", "Bob", "Carol"]},
        ],
    })
    assert result["currency"] == "TWD"
    assert result["total_expenses"] == 1500
    assert result["num_settlements"] == 2
    by_person = {s["participant"]: s for s in result["summary"]}
    assert by_person["Alice"]["balance"] == 700
    assert by_person["Bob"]["balance"] == -200
    assert by_person["Carol"]["balance"] == -500
    settlements = result["settlements"]
    assert any(s["from"] == "Carol" and s["to"] == "Alice" and s["amount"] == 500 for s in settlements)
    assert any(s["from"] == "Bob" and s["to"] == "Alice" and s["amount"] == 200 for s in settlements)


def test_two_people_even_split():
    result = split_settle({
        "currency": "USD",
        "participants": ["A", "B"],
        "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "B"]}],
    })
    assert result["num_settlements"] == 1
    assert result["settlements"][0] == {"from": "B", "to": "A", "amount": 50.0}


def test_remainder_distribution():
    result = split_settle({
        "currency": "TWD",
        "participants": ["A", "B", "C"],
        "expenses": [{"paid_by": "A", "amount": 1.00, "split_among": ["A", "B", "C"]}],
    })
    total_owed = sum(s["total_owed"] for s in result["summary"])
    assert round(total_owed * 100) == 100


def test_already_settled():
    result = split_settle({
        "currency": "TWD",
        "participants": ["A", "B"],
        "expenses": [
            {"paid_by": "A", "amount": 50, "split_among": ["A"]},
            {"paid_by": "B", "amount": 50, "split_among": ["B"]},
        ],
    })
    assert result["num_settlements"] == 0


def test_payer_not_in_split():
    result = split_settle({
        "currency": "TWD",
        "participants": ["A", "B", "C"],
        "expenses": [{"paid_by": "A", "amount": 300, "split_among": ["B", "C"]}],
    })
    by_person = {s["participant"]: s for s in result["summary"]}
    assert by_person["A"]["balance"] == 300
    assert by_person["B"]["balance"] == -150
    assert by_person["C"]["balance"] == -150


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

def test_missing_currency():
    with pytest.raises(ValueError, match="currency"):
        split_settle({"participants": ["A", "B"], "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "B"]}]})


def test_too_few_participants():
    with pytest.raises(ValueError):
        split_settle({"currency": "TWD", "participants": ["A"], "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A"]}]})


def test_too_many_participants():
    participants = [str(i) for i in range(21)]
    with pytest.raises(ValueError, match="20"):
        split_settle({"currency": "TWD", "participants": participants, "expenses": [{"paid_by": "0", "amount": 100, "split_among": ["0", "1"]}]})


def test_paid_by_not_in_participants():
    with pytest.raises(ValueError, match="paid_by"):
        split_settle({
            "currency": "TWD",
            "participants": ["A", "B"],
            "expenses": [{"paid_by": "X", "amount": 100, "split_among": ["A", "B"]}],
        })


def test_split_among_not_in_participants():
    with pytest.raises(ValueError, match="not in participants"):
        split_settle({
            "currency": "TWD",
            "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "X"]}],
        })


def test_zero_amount():
    with pytest.raises(ValueError):
        split_settle({
            "currency": "TWD",
            "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": 0, "split_among": ["A", "B"]}],
        })


# ---------------------------------------------------------------------------
# Lambda handler tests (use API_KEY to bypass payment for these basic tests)
# ---------------------------------------------------------------------------

VALID_BODY = json.dumps({
    "currency": "TWD",
    "participants": ["Alice", "Bob"],
    "expenses": [{"paid_by": "Alice", "amount": 200, "split_among": ["Alice", "Bob"]}],
})


def test_lambda_handler_success(monkeypatch):
    monkeypatch.setenv("API_KEY", "testkey")
    event = {"rawPath": "/split_settle", "headers": {"x-api-key": "testkey"}, "body": VALID_BODY}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["num_settlements"] == 1


def test_lambda_handler_bad_request(monkeypatch):
    monkeypatch.setenv("API_KEY", "testkey")
    event = {"rawPath": "/split_settle", "headers": {"x-api-key": "testkey"}, "body": json.dumps({"currency": "TWD"})}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 400
    assert "error" in json.loads(response["body"])


def test_lambda_handler_empty_body(monkeypatch):
    monkeypatch.setenv("API_KEY", "testkey")
    event = {"rawPath": "/split_settle", "headers": {"x-api-key": "testkey"}, "body": None}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 400


# ---------------------------------------------------------------------------
# API Key auth tests
# ---------------------------------------------------------------------------

def test_api_key_accepted(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret123")
    event = {
        "rawPath": "/split_settle",
        "headers": {"x-api-key": "secret123"},
        "body": VALID_BODY,
    }
    assert lambda_handler(event, {})["statusCode"] == 200


def test_api_key_rejected(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret123")
    event = {"rawPath": "/split_settle", "headers": {"x-api-key": "wrong"}, "body": VALID_BODY}
    assert lambda_handler(event, {})["statusCode"] == 403


def test_api_key_missing_header(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret123")
    event = {"rawPath": "/split_settle", "headers": {}, "body": VALID_BODY}
    assert lambda_handler(event, {})["statusCode"] == 403


def test_no_auth_configured_returns_402(monkeypatch):
    """When no API key and no payment header, return 402 with payment instructions."""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SECRET_ARN", raising=False)
    monkeypatch.delenv("PAYMENTS_TABLE", raising=False)
    event = {"rawPath": "/split_settle", "headers": {}, "body": VALID_BODY}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 402
    body = json.loads(response["body"])
    assert "x402" in body
    assert body["x402"]["recipient"] == handler.PAYMENT_RECIPIENT
    assert body["x402"]["network"] == "base-mainnet"


# ---------------------------------------------------------------------------
# x402 payment tests
# ---------------------------------------------------------------------------

def _make_receipt(*, status="0x1", block_number="0xa", amount=1000,
                  recipient=None, token=None):
    """Build a fake Alchemy eth_getTransactionReceipt result."""
    recipient = recipient or handler.PAYMENT_RECIPIENT
    token = token or handler.PAYMENT_TOKEN_CONTRACT
    recipient_padded = "0x" + "000000000000000000000000" + recipient[2:].lower()
    return {
        "status": status,
        "blockNumber": block_number,
        "logs": [{
            "address": token,
            "topics": [
                handler.TRANSFER_EVENT_SIG,
                "0x000000000000000000000000abcdef1234567890abcdef1234567890abcdef12",  # from
                recipient_padded,
            ],
            "data": hex(amount),
        }],
    }


def _patch_rpc(monkeypatch, receipt, current_block="0xb"):
    """Patch _rpc_call to return fake receipt and block number."""
    def fake_rpc(method, params):
        if method == "eth_getTransactionReceipt":
            return {"result": receipt}
        if method == "eth_blockNumber":
            return {"result": current_block}
        return {"result": None}
    monkeypatch.setattr(handler, "_rpc_call", fake_rpc)


def test_x402_valid_payment_returns_200(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SECRET_ARN", raising=False)
    monkeypatch.setenv("PAYMENTS_TABLE", "test-table")
    monkeypatch.setattr(handler, "_is_tx_used", lambda tx: False)
    monkeypatch.setattr(handler, "_mark_tx_used", lambda tx: None)
    _patch_rpc(monkeypatch, _make_receipt(amount=1000))

    event = {
        "rawPath": "/split_settle",
        "headers": {"x-payment": json.dumps({"tx_hash": "0xabc123", "network": "base-mainnet"})},
        "body": VALID_BODY,
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200


def test_x402_malformed_header_returns_402(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    event = {"rawPath": "/split_settle", "headers": {"x-payment": "not-json"}, "body": VALID_BODY}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 402
    assert "malformed" in json.loads(response["body"]).get("reason", "")


def test_x402_missing_tx_hash_returns_402(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    event = {
        "rawPath": "/split_settle",
        "headers": {"x-payment": json.dumps({"network": "base-mainnet"})},
        "body": VALID_BODY,
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 402


def test_x402_wrong_network_returns_402(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(handler, "_is_tx_used", lambda tx: False)
    _patch_rpc(monkeypatch, _make_receipt())
    event = {
        "rawPath": "/split_settle",
        "headers": {"x-payment": json.dumps({"tx_hash": "0xabc", "network": "ethereum-mainnet"})},
        "body": VALID_BODY,
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 402
    assert "wrong network" in json.loads(response["body"]).get("reason", "")


def test_x402_replay_attack_returns_402(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(handler, "_is_tx_used", lambda tx: True)  # already used
    event = {
        "rawPath": "/split_settle",
        "headers": {"x-payment": json.dumps({"tx_hash": "0xabc", "network": "base-mainnet"})},
        "body": VALID_BODY,
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 402
    assert "already used" in json.loads(response["body"]).get("reason", "")


def test_x402_tx_not_found_returns_402(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(handler, "_is_tx_used", lambda tx: False)
    monkeypatch.setattr(handler, "_rpc_call", lambda m, p: {"result": None})
    event = {
        "rawPath": "/split_settle",
        "headers": {"x-payment": json.dumps({"tx_hash": "0xabc", "network": "base-mainnet"})},
        "body": VALID_BODY,
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 402
    assert "not found" in json.loads(response["body"]).get("reason", "")


def test_x402_reverted_tx_returns_402(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(handler, "_is_tx_used", lambda tx: False)
    _patch_rpc(monkeypatch, _make_receipt(status="0x0"))
    event = {
        "rawPath": "/split_settle",
        "headers": {"x-payment": json.dumps({"tx_hash": "0xabc", "network": "base-mainnet"})},
        "body": VALID_BODY,
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 402
    assert "reverted" in json.loads(response["body"]).get("reason", "")


def test_x402_amount_too_low_returns_402(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(handler, "_is_tx_used", lambda tx: False)
    _patch_rpc(monkeypatch, _make_receipt(amount=999))  # 1 below minimum
    event = {
        "rawPath": "/split_settle",
        "headers": {"x-payment": json.dumps({"tx_hash": "0xabc", "network": "base-mainnet"})},
        "body": VALID_BODY,
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 402
    assert "too low" in json.loads(response["body"]).get("reason", "")


def test_x402_wrong_recipient_returns_402(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(handler, "_is_tx_used", lambda tx: False)
    _patch_rpc(monkeypatch, _make_receipt(recipient="0x0000000000000000000000000000000000000001"))
    event = {
        "rawPath": "/split_settle",
        "headers": {"x-payment": json.dumps({"tx_hash": "0xabc", "network": "base-mainnet"})},
        "body": VALID_BODY,
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 402
    assert "no valid USDC transfer" in json.loads(response["body"]).get("reason", "")


def test_x402_overrides_api_key_when_payment_valid(monkeypatch):
    """X-PAYMENT header takes precedence over x-api-key."""
    monkeypatch.setenv("API_KEY", "secret123")
    monkeypatch.setenv("PAYMENTS_TABLE", "test-table")
    monkeypatch.setattr(handler, "_is_tx_used", lambda tx: False)
    monkeypatch.setattr(handler, "_mark_tx_used", lambda tx: None)
    _patch_rpc(monkeypatch, _make_receipt(amount=1000))

    event = {
        "rawPath": "/split_settle",
        "headers": {
            "x-payment": json.dumps({"tx_hash": "0xabc", "network": "base-mainnet"}),
            # no x-api-key — but x402 path should succeed anyway
        },
        "body": VALID_BODY,
    }
    assert lambda_handler(event, {})["statusCode"] == 200


# ---------------------------------------------------------------------------
# OpenAPI and health tests
# ---------------------------------------------------------------------------

def test_openapi_endpoint():
    event = {"rawPath": "/openapi.json"}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    schema = json.loads(response["body"])
    assert schema["openapi"].startswith("3.")
    assert "/v1/split_settle" in schema["paths"]


def test_health_endpoint():
    event = {"rawPath": "/health"}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["status"] == "ok"


# ---------------------------------------------------------------------------
# HTTP method checking tests
# ---------------------------------------------------------------------------

def test_method_not_allowed_get_split_settle(monkeypatch):
    monkeypatch.setenv("API_KEY", "testkey")
    event = {
        "rawPath": "/v1/split_settle",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {"x-api-key": "testkey"},
        "body": VALID_BODY,
    }
    assert lambda_handler(event, {})["statusCode"] == 405


def test_method_not_allowed_post_health():
    event = {
        "rawPath": "/health",
        "requestContext": {"http": {"method": "POST"}},
    }
    assert lambda_handler(event, {})["statusCode"] == 405


# ---------------------------------------------------------------------------
# _get_secret tests
# ---------------------------------------------------------------------------

def test_get_secret_from_env(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "direct-value")
    result = handler._get_secret("MY_SECRET", "MY_SECRET_ARN")
    assert result == "direct-value"


# ---------------------------------------------------------------------------
# EIP-55 validation tests
# ---------------------------------------------------------------------------

def test_eip55_valid_address():
    # Known valid EIP-55 checksummed address (Ethereum foundation)
    assert handler._validate_checksum_address("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed") is True


def test_eip55_invalid_checksum():
    # Same address but wrong case on one character
    assert handler._validate_checksum_address("0x5aaeb6053F3E94C9b9A09f33669435E7Ef1BeAed") is False


def test_eip55_invalid_format_short():
    assert handler._validate_checksum_address("0x1234") is False


def test_eip55_invalid_format_non_hex():
    assert handler._validate_checksum_address("0xGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG") is False


def test_eip55_not_string():
    assert handler._validate_checksum_address(12345) is False


# ---------------------------------------------------------------------------
# ABI encoding tests
# ---------------------------------------------------------------------------

def test_encode_transfer_calldata_known_vector():
    # transfer(0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed, 1000000)
    result = handler._encode_transfer_calldata(
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", 1000000
    )
    assert result.startswith("0xa9059cbb")
    assert len(result) == 2 + 8 + 64 + 64  # 0x + selector + addr + amount
    # Verify amount encoding (1000000 = 0xf4240)
    assert result.endswith("f4240".zfill(64))


def test_encode_transfer_calldata_min_amount():
    result = handler._encode_transfer_calldata(
        "0x0000000000000000000000000000000000000001", 1
    )
    assert result.endswith("1".zfill(64))


def test_encode_transfer_calldata_max_uint256():
    max_val = 2**256 - 1
    result = handler._encode_transfer_calldata(
        "0x0000000000000000000000000000000000000001", max_val
    )
    assert "f" * 64 in result


# ---------------------------------------------------------------------------
# POST /v1/groups tests
# ---------------------------------------------------------------------------

# Known valid EIP-55 addresses for testing
_ALICE_WALLET = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
_BOB_WALLET = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"

# Fake DynamoDB for groups
_fake_groups_db = {}


def _fake_groups_query(TableName, KeyConditionExpression, ExpressionAttributeValues, **kwargs):
    pk = ExpressionAttributeValues[":pk"]["S"]
    items = [v for k, v in _fake_groups_db.items() if k.startswith(pk + "|")]
    limit = kwargs.get("Limit")
    if limit:
        items = items[:limit]
    return {"Items": items}


def _fake_groups_put(TableName, Item, **kwargs):
    pk = Item["PK"]["S"]
    sk = Item["SK"]["S"]
    _fake_groups_db[f"{pk}|{sk}"] = Item


class FakeGroupsDynamoDB:
    def query(self, **kwargs):
        return _fake_groups_query(**kwargs)

    def put_item(self, **kwargs):
        return _fake_groups_put(**kwargs)

    class exceptions:
        class ConditionalCheckFailedException(Exception):
            pass


@pytest.fixture
def groups_env(monkeypatch):
    """Set up groups environment with fake DynamoDB."""
    monkeypatch.setenv("API_KEY", "testkey")
    monkeypatch.setenv("GROUPS_TABLE", "test-groups")
    _fake_groups_db.clear()
    monkeypatch.setattr(handler, "_get_group_participants",
                        lambda gid: {
                            k.split("PARTICIPANT#")[1]: v.get("wallet_address", {}).get("S", "")
                            for k, v in _fake_groups_db.items()
                            if k.startswith(f"GROUP#{gid}|PARTICIPANT#")
                        })

    # Create a fake boto3 module that works even if boto3 isn't installed
    import types
    import unittest.mock

    mock_client = unittest.mock.MagicMock()
    mock_client.query = lambda **kwargs: _fake_groups_query(**kwargs)
    mock_client.put_item = lambda **kwargs: _fake_groups_put(**kwargs)

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda service, **kwargs: mock_client

    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    yield


def _groups_event(body):
    return {
        "rawPath": "/v1/groups",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"x-api-key": "testkey"},
        "body": json.dumps(body),
    }


def test_create_group_success(groups_env):
    body = {
        "group_id": "trip-tokyo-2026",
        "participants": [
            {"name": "Alice", "wallet_address": _ALICE_WALLET},
            {"name": "Bob", "wallet_address": _BOB_WALLET},
        ],
    }
    response = lambda_handler(_groups_event(body), {})
    assert response["statusCode"] == 200
    result = json.loads(response["body"])
    assert result["group_id"] == "trip-tokyo-2026"
    assert result["participants"] == 2


def test_create_group_duplicate_409(groups_env):
    body = {
        "group_id": "trip-tokyo-2026",
        "participants": [
            {"name": "Alice", "wallet_address": _ALICE_WALLET},
            {"name": "Bob", "wallet_address": _BOB_WALLET},
        ],
    }
    lambda_handler(_groups_event(body), {})
    response = lambda_handler(_groups_event(body), {})
    assert response["statusCode"] == 409
    assert "GROUP_EXISTS" in json.loads(response["body"]).get("code", "")


def test_create_group_invalid_wallet(groups_env):
    body = {
        "group_id": "test-group",
        "participants": [
            {"name": "Alice", "wallet_address": "not-an-address"},
            {"name": "Bob", "wallet_address": _BOB_WALLET},
        ],
    }
    response = lambda_handler(_groups_event(body), {})
    assert response["statusCode"] == 400
    assert "invalid wallet" in json.loads(response["body"])["error"].lower()


def test_create_group_bad_checksum(groups_env):
    # Valid hex but wrong EIP-55 checksum
    body = {
        "group_id": "test-group",
        "participants": [
            {"name": "Alice", "wallet_address": "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"},
            {"name": "Bob", "wallet_address": _BOB_WALLET},
        ],
    }
    response = lambda_handler(_groups_event(body), {})
    assert response["statusCode"] == 400


def test_create_group_missing_fields(groups_env):
    response = lambda_handler(_groups_event({}), {})
    assert response["statusCode"] == 400


def test_create_group_invalid_group_id(groups_env):
    body = {
        "group_id": "INVALID_ID!@#",
        "participants": [
            {"name": "Alice", "wallet_address": _ALICE_WALLET},
            {"name": "Bob", "wallet_address": _BOB_WALLET},
        ],
    }
    response = lambda_handler(_groups_event(body), {})
    assert response["statusCode"] == 400


# ---------------------------------------------------------------------------
# split_settle with group_id tests
# ---------------------------------------------------------------------------

def test_split_settle_with_group_id(groups_env, monkeypatch):
    monkeypatch.delenv("SECRET_ARN", raising=False)
    # Create group first
    body = {
        "group_id": "test-settle",
        "participants": [
            {"name": "Alice", "wallet_address": _ALICE_WALLET},
            {"name": "Bob", "wallet_address": _BOB_WALLET},
        ],
    }
    lambda_handler(_groups_event(body), {})

    # Now split_settle with group_id
    settle_body = {
        "currency": "USD",
        "group_id": "test-settle",
        "participants": ["Alice", "Bob"],
        "expenses": [{"paid_by": "Alice", "amount": 100, "split_among": ["Alice", "Bob"]}],
    }
    event = {
        "rawPath": "/v1/split_settle",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"x-api-key": "testkey"},
        "body": json.dumps(settle_body),
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    result = json.loads(response["body"])
    assert "execution" in result
    assert result["execution"]["network"] == "base-sepolia"
    assert len(result["execution"]["transfers"]) == 1
    transfer = result["execution"]["transfers"][0]
    assert transfer["calldata"].startswith("0xa9059cbb")
    assert transfer["to_wallet"] == _ALICE_WALLET


def test_split_settle_group_not_found(monkeypatch):
    monkeypatch.setenv("API_KEY", "testkey")
    monkeypatch.setenv("GROUPS_TABLE", "test-groups")
    monkeypatch.setattr(handler, "_get_group_participants", lambda gid: {})

    settle_body = {
        "currency": "USD",
        "group_id": "nonexistent",
        "participants": ["Alice", "Bob"],
        "expenses": [{"paid_by": "Alice", "amount": 100, "split_among": ["Alice", "Bob"]}],
    }
    event = {
        "rawPath": "/v1/split_settle",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"x-api-key": "testkey"},
        "body": json.dumps(settle_body),
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 400
    assert "not found" in json.loads(response["body"])["error"]


def test_split_settle_participant_mismatch(monkeypatch):
    monkeypatch.setenv("API_KEY", "testkey")
    monkeypatch.setenv("GROUPS_TABLE", "test-groups")
    # Group only has Alice, not Bob
    monkeypatch.setattr(handler, "_get_group_participants",
                        lambda gid: {"Alice": _ALICE_WALLET})

    settle_body = {
        "currency": "USD",
        "group_id": "test-group",
        "participants": ["Alice", "Bob"],
        "expenses": [{"paid_by": "Alice", "amount": 100, "split_among": ["Alice", "Bob"]}],
    }
    event = {
        "rawPath": "/v1/split_settle",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"x-api-key": "testkey"},
        "body": json.dumps(settle_body),
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 400
    assert "not found in group" in json.loads(response["body"])["error"]


# ---------------------------------------------------------------------------
# Home page tests
# ---------------------------------------------------------------------------

def test_home_page_returns_html():
    event = {"rawPath": "/", "requestContext": {"http": {"method": "GET"}}}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"].startswith("text/html")
    assert "SplitSettle" in response["body"]
    assert "preact" in response["body"].lower()


def test_home_no_auth_needed():
    """Home page works without API key."""
    event = {"rawPath": "/", "requestContext": {"http": {"method": "GET"}}, "headers": {}}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200


# ---------------------------------------------------------------------------
# Share endpoint tests
# ---------------------------------------------------------------------------

_fake_shares_db = {}


def _fake_share_save(share_id, request_body, result):
    _fake_shares_db[share_id] = {
        "request_body": request_body,
        "result": result,
        "created_at": "2026-04-03T10:00:00Z",
        "ttl_expiry": int(time.time()) + 86400 * 30,
    }


def _fake_share_get(share_id):
    return _fake_shares_db.get(share_id)


@pytest.fixture
def share_env(monkeypatch):
    _fake_shares_db.clear()
    monkeypatch.setenv("GROUPS_TABLE", "test-groups")
    monkeypatch.setattr(handler, "_save_share", _fake_share_save)
    monkeypatch.setattr(handler, "_get_share", _fake_share_get)
    yield


def test_share_creates_and_returns_id(share_env):
    body = {"currency": "TWD", "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "B"]}]}
    event = {"rawPath": "/v1/share", "requestContext": {"http": {"method": "POST"}},
             "headers": {}, "body": json.dumps(body)}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    result = json.loads(response["body"])
    assert "share_id" in result
    assert result["url"].startswith("/s/")


def test_share_invalid_body(share_env):
    body = {"currency": "TWD"}  # missing participants
    event = {"rawPath": "/v1/share", "requestContext": {"http": {"method": "POST"}},
             "headers": {}, "body": json.dumps(body)}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 400


def test_share_no_auth_needed(share_env, monkeypatch):
    """Share endpoint works without API key."""
    monkeypatch.setenv("API_KEY", "secret123")
    body = {"currency": "USD", "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": 50, "split_among": ["A", "B"]}]}
    event = {"rawPath": "/v1/share", "requestContext": {"http": {"method": "POST"}},
             "headers": {}, "body": json.dumps(body)}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200


# ---------------------------------------------------------------------------
# Share page tests
# ---------------------------------------------------------------------------

def test_share_page_renders(share_env):
    # Create a share first
    _fake_share_save("test1234", {"currency": "TWD"}, {
        "currency": "TWD", "total_expenses": 1500,
        "settlements": [{"from": "Bob", "to": "Alice", "amount": 500}],
        "summary": [{"participant": "Alice"}, {"participant": "Bob"}],
        "num_settlements": 1,
    })
    event = {"rawPath": "/s/test1234", "requestContext": {"http": {"method": "GET"}}, "headers": {}}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"].startswith("text/html")
    assert "Alice" in response["body"]
    assert "Bob" in response["body"]


def test_share_page_has_og_tags(share_env):
    _fake_share_save("og-test1", {"currency": "USD"}, {
        "currency": "USD", "total_expenses": 100,
        "settlements": [{"from": "B", "to": "A", "amount": 50}],
        "summary": [{"participant": "A"}, {"participant": "B"}],
        "num_settlements": 1,
    })
    event = {"rawPath": "/s/og-test1", "requestContext": {"http": {"method": "GET"}}, "headers": {}}
    response = lambda_handler(event, {})
    assert "og:title" in response["body"]
    assert "og:description" in response["body"]


def test_share_page_not_found(share_env):
    event = {"rawPath": "/s/nonexist", "requestContext": {"http": {"method": "GET"}}, "headers": {}}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 404
    assert "expired" in response["body"].lower() or "not found" in response["body"].lower()


def test_share_page_expired(share_env):
    _fake_shares_db["expired1"] = {
        "request_body": {}, "result": {},
        "created_at": "2026-01-01T00:00:00Z",
        "ttl_expiry": 1,  # expired long ago
    }
    event = {"rawPath": "/s/expired1", "requestContext": {"http": {"method": "GET"}}, "headers": {}}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 404


# ---------------------------------------------------------------------------
# wallet_address optional tests
# ---------------------------------------------------------------------------

def test_create_group_no_wallet(groups_env):
    body = {
        "group_id": "no-wallet-group",
        "participants": [
            {"name": "Alice"},
            {"name": "Bob"},
        ],
    }
    response = lambda_handler(_groups_event(body), {})
    assert response["statusCode"] == 200
    result = json.loads(response["body"])
    assert result["participants"] == 2


def test_split_settle_group_no_wallet_skips_execution(monkeypatch):
    monkeypatch.setenv("API_KEY", "testkey")
    monkeypatch.setenv("GROUPS_TABLE", "test-groups")
    monkeypatch.setattr(handler, "_get_group_participants",
                        lambda gid: {"Alice": "", "Bob": ""})  # no wallets

    settle_body = {
        "currency": "USD",
        "group_id": "no-wallet",
        "participants": ["Alice", "Bob"],
        "expenses": [{"paid_by": "Alice", "amount": 100, "split_among": ["Alice", "Bob"]}],
    }
    event = {
        "rawPath": "/v1/split_settle",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"x-api-key": "testkey"},
        "body": json.dumps(settle_body),
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    result = json.loads(response["body"])
    assert "execution" not in result  # no wallets → no execution block
    assert result["num_settlements"] == 1  # but settlements still calculated


# ---------------------------------------------------------------------------
# Regression: agent API still requires auth
# ---------------------------------------------------------------------------

def test_agent_api_still_requires_auth(monkeypatch):
    """Verify /v1/split_settle still requires API key (regression test)."""
    monkeypatch.setenv("API_KEY", "secret123")
    event = {
        "rawPath": "/v1/split_settle",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {},  # no API key
        "body": json.dumps({"currency": "USD", "participants": ["A", "B"],
                           "expenses": [{"paid_by": "A", "amount": 10, "split_among": ["A", "B"]}]}),
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 403


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------

def test_duplicate_participant_name_rejected():
    """Duplicate names should be rejected."""
    with pytest.raises(ValueError, match="duplicate"):
        handler.split_settle({
            "currency": "TWD", "participants": ["Alice", "Alice", "Bob"],
            "expenses": [{"paid_by": "Alice", "amount": 300, "split_among": ["Alice", "Bob"]}],
        })


def test_emoji_participant_name():
    """Emoji names should work fine."""
    result = handler.split_settle({
        "currency": "TWD", "participants": ["👻", "🎃"],
        "expenses": [{"paid_by": "👻", "amount": 100, "split_among": ["👻", "🎃"]}],
    })
    assert result["num_settlements"] == 1
    assert result["settlements"][0]["from"] == "🎃"
    assert result["settlements"][0]["to"] == "👻"


def test_special_chars_in_name():
    """Names with HTML-like chars should not break."""
    result = handler.split_settle({
        "currency": "USD", "participants": ["<script>alert</script>", "Bob"],
        "expenses": [{"paid_by": "<script>alert</script>", "amount": 50, "split_among": ["<script>alert</script>", "Bob"]}],
    })
    assert result["num_settlements"] == 1


def test_name_at_limit():
    """50 char name should work."""
    name_50 = "A" * 50
    result = handler.split_settle({
        "currency": "USD", "participants": [name_50, "B"],
        "expenses": [{"paid_by": name_50, "amount": 100, "split_among": [name_50, "B"]}],
    })
    assert result["settlements"][0]["to"] == name_50


def test_name_over_limit_rejected():
    """51+ char name should be rejected."""
    with pytest.raises(ValueError, match="too long"):
        handler.split_settle({
            "currency": "USD", "participants": ["A" * 51, "B"],
            "expenses": [{"paid_by": "A" * 51, "amount": 100, "split_among": ["A" * 51, "B"]}],
        })


def test_zero_amount_rejected():
    """Amount 0 should be rejected."""
    with pytest.raises(ValueError):
        handler.split_settle({
            "currency": "TWD", "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": 0, "split_among": ["A", "B"]}],
        })


def test_negative_amount_rejected():
    """Negative amount should be rejected."""
    with pytest.raises(ValueError):
        handler.split_settle({
            "currency": "TWD", "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": -50, "split_among": ["A", "B"]}],
        })


def test_very_large_amount():
    """Very large amount should not overflow."""
    result = handler.split_settle({
        "currency": "TWD", "participants": ["A", "B"],
        "expenses": [{"paid_by": "A", "amount": 999999999, "split_among": ["A", "B"]}],
    })
    assert result["total_expenses"] == 999999999
    assert result["settlements"][0]["amount"] == 499999999.5


def test_decimal_amount():
    """Decimal amounts should work with cent precision."""
    result = handler.split_settle({
        "currency": "USD", "participants": ["A", "B"],
        "expenses": [{"paid_by": "A", "amount": 33.33, "split_among": ["A", "B"]}],
    })
    assert result["total_expenses"] == 33.33


def test_indivisible_amount_three_ways():
    """100 / 3 = 33.33... — remainder should be distributed."""
    result = handler.split_settle({
        "currency": "TWD", "participants": ["A", "B", "C"],
        "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "B", "C"]}],
    })
    total_owed = sum(s["total_owed"] for s in result["summary"])
    assert round(total_owed * 100) == 10000  # cents add up exactly


def test_split_among_only_self():
    """Splitting only among the payer means no settlements needed."""
    result = handler.split_settle({
        "currency": "TWD", "participants": ["A", "B"],
        "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A"]}],
    })
    assert result["num_settlements"] == 0


def test_all_expenses_same_payer():
    """All expenses paid by one person, split among all."""
    result = handler.split_settle({
        "currency": "TWD", "participants": ["A", "B", "C"],
        "expenses": [
            {"paid_by": "A", "amount": 300, "split_among": ["A", "B", "C"]},
            {"paid_by": "A", "amount": 150, "split_among": ["A", "B", "C"]},
        ],
    })
    assert result["total_expenses"] == 450
    assert result["num_settlements"] == 2
    # B and C each owe A
    for s in result["settlements"]:
        assert s["to"] == "A"


def test_already_balanced_no_settlements():
    """Everyone paid their own share — 0 settlements."""
    result = handler.split_settle({
        "currency": "TWD", "participants": ["A", "B"],
        "expenses": [
            {"paid_by": "A", "amount": 100, "split_among": ["A"]},
            {"paid_by": "B", "amount": 100, "split_among": ["B"]},
        ],
    })
    assert result["num_settlements"] == 0


def test_max_participants_20():
    """20 participants should work (upper limit)."""
    names = [f"P{i}" for i in range(20)]
    result = handler.split_settle({
        "currency": "TWD", "participants": names,
        "expenses": [{"paid_by": "P0", "amount": 2000, "split_among": names}],
    })
    assert result["total_expenses"] == 2000
    assert len(result["summary"]) == 20


def test_21_participants_rejected():
    """21 participants should be rejected."""
    names = [f"P{i}" for i in range(21)]
    with pytest.raises(ValueError, match="20"):
        handler.split_settle({
            "currency": "TWD", "participants": names,
            "expenses": [{"paid_by": "P0", "amount": 100, "split_among": names}],
        })


def test_share_empty_body(share_env):
    """POST /v1/share with empty body should 400."""
    event = {"rawPath": "/v1/share", "requestContext": {"http": {"method": "POST"}},
             "headers": {}, "body": "{}"}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 400


def test_share_page_no_id():
    """GET /s/ with no ID should 404."""
    event = {"rawPath": "/s/", "requestContext": {"http": {"method": "GET"}}, "headers": {}}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 404


def test_share_page_xss_prevention(share_env):
    """Names with <script> should be HTML-escaped in share page."""
    _fake_share_save("xss-test", {"currency": "USD"}, {
        "currency": "USD", "total_expenses": 100,
        "settlements": [{"from": "<script>alert(1)</script>", "to": "Bob", "amount": 50}],
        "summary": [{"participant": "<script>alert(1)</script>"}, {"participant": "Bob"}],
        "num_settlements": 1,
    })
    event = {"rawPath": "/s/xss-test", "requestContext": {"http": {"method": "GET"}}, "headers": {}}
    response = lambda_handler(event, {})
    body = response["body"]
    assert "<script>alert(1)</script>" not in body  # user input must be escaped
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body  # should show escaped version


def test_share_page_special_chars_id(share_env):
    """GET /s/<script> should 404 not crash."""
    event = {"rawPath": "/s/<script>alert(1)</script>", "requestContext": {"http": {"method": "GET"}}, "headers": {}}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 404


# ---------------------------------------------------------------------------
# v3 Unit tests: new features
# ---------------------------------------------------------------------------

def test_generate_share_id_length():
    """Share IDs should be 8 chars."""
    sid = handler._generate_share_id()
    assert len(sid) == 8
    assert isinstance(sid, str)


def test_generate_share_id_unique():
    """Two share IDs should not be identical."""
    ids = {handler._generate_share_id() for _ in range(20)}
    assert len(ids) == 20


def test_docs_page_returns_html():
    event = {"rawPath": "/docs", "requestContext": {"http": {"method": "GET"}}}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"].startswith("text/html")
    assert "swagger" in response["body"].lower() or "Swagger" in response["body"]


def test_home_page_has_importmap():
    """Home page should include boring-avatars in importmap."""
    event = {"rawPath": "/", "requestContext": {"http": {"method": "GET"}}}
    response = lambda_handler(event, {})
    assert "boring-avatars" in response["body"]
    assert "importmap" in response["body"]


def test_home_page_has_app_name():
    """Home page should contain the app name in i18n."""
    event = {"rawPath": "/", "requestContext": {"http": {"method": "GET"}}}
    response = lambda_handler(event, {})
    body = response["body"]
    # Check all three language titles are in the JS
    assert "Split Senpai" in body
    assert "分帳仙貝" in body
    assert "割り勘先輩" in body


def test_share_with_lang_param(share_env):
    """POST /v1/share with lang=zh-TW should include lang in URL."""
    body = {"currency": "TWD", "participants": ["A", "B"], "lang": "zh-TW",
            "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "B"]}]}
    event = {"rawPath": "/v1/share", "requestContext": {"http": {"method": "POST"}},
             "headers": {}, "body": json.dumps(body)}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    result = json.loads(response["body"])
    assert "lang=zh-TW" in result["url"]


def test_share_with_lang_en_no_param(share_env):
    """POST /v1/share with lang=en should NOT include lang in URL (default)."""
    body = {"currency": "USD", "participants": ["A", "B"], "lang": "en",
            "expenses": [{"paid_by": "A", "amount": 50, "split_among": ["A", "B"]}]}
    event = {"rawPath": "/v1/share", "requestContext": {"http": {"method": "POST"}},
             "headers": {}, "body": json.dumps(body)}
    response = lambda_handler(event, {})
    result = json.loads(response["body"])
    assert "lang=" not in result["url"]


def test_share_page_i18n_zh(share_env):
    """Share page with lang=zh-TW should show Chinese title."""
    _fake_share_save("zh-test", {"currency": "TWD"}, {
        "currency": "TWD", "total_expenses": 100,
        "settlements": [{"from": "B", "to": "A", "amount": 50}],
        "summary": [{"participant": "A"}, {"participant": "B"}],
        "num_settlements": 1,
    })
    event = {"rawPath": "/s/zh-test", "requestContext": {"http": {"method": "GET"}},
             "headers": {}, "queryStringParameters": {"lang": "zh-TW"}}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    assert "分帳仙貝" in response["body"]
    assert "也要分帳？" in response["body"]


def test_share_page_i18n_ja(share_env):
    """Share page with lang=ja should show Japanese title."""
    _fake_share_save("ja-test", {"currency": "JPY"}, {
        "currency": "JPY", "total_expenses": 1000,
        "settlements": [{"from": "B", "to": "A", "amount": 500}],
        "summary": [{"participant": "A"}, {"participant": "B"}],
        "num_settlements": 1,
    })
    event = {"rawPath": "/s/ja-test", "requestContext": {"http": {"method": "GET"}},
             "headers": {}, "queryStringParameters": {"lang": "ja"}}
    response = lambda_handler(event, {})
    assert "割り勘先輩" in response["body"]


def test_share_page_has_identity_card_with_participants(share_env):
    """Share page should bootstrap participants for the identity card."""
    _fake_share_save("me-test", {"currency": "USD"}, {
        "currency": "USD", "total_expenses": 300,
        "settlements": [{"from": "Carol", "to": "Alice", "amount": 100}],
        "summary": [{"participant": "Alice"}, {"participant": "Bob"}, {"participant": "Carol"}],
        "num_settlements": 1,
    })
    event = {"rawPath": "/s/me-test", "requestContext": {"http": {"method": "GET"}}, "headers": {}}
    response = lambda_handler(event, {})
    body = response["body"]
    assert "identity-card" in body
    assert "Alice" in body
    assert "Bob" in body
    assert "Carol" in body
    assert "renderIdentityCard" in body  # JS function exists


def test_share_page_has_split_senpai_title(share_env):
    """Share page should use Split Senpai as default title."""
    _fake_share_save("title-test", {"currency": "USD"}, {
        "currency": "USD", "total_expenses": 100,
        "settlements": [{"from": "B", "to": "A", "amount": 50}],
        "summary": [{"participant": "A"}, {"participant": "B"}],
        "num_settlements": 1,
    })
    event = {"rawPath": "/s/title-test", "requestContext": {"http": {"method": "GET"}}, "headers": {}}
    response = lambda_handler(event, {})
    assert "Split Senpai" in response["body"]


def test_share_page_g_color_scheme(share_env):
    """Share page should use G color scheme (deep teal)."""
    _fake_share_save("color-test", {"currency": "USD"}, {
        "currency": "USD", "total_expenses": 100,
        "settlements": [{"from": "B", "to": "A", "amount": 50}],
        "summary": [{"participant": "A"}, {"participant": "B"}],
        "num_settlements": 1,
    })
    event = {"rawPath": "/s/color-test", "requestContext": {"http": {"method": "GET"}}, "headers": {}}
    response = lambda_handler(event, {})
    body = response["body"]
    assert "#2d4a4a" in body or "#d5d0c8" in body  # G color scheme


def test_render_share_page_settlement_html():
    """_render_share_page should produce settlement divs with animation delay."""
    result = {
        "currency": "TWD", "total_expenses": 500,
        "settlements": [
            {"from": "Bob", "to": "Alice", "amount": 300},
            {"from": "Carol", "to": "Alice", "amount": 200},
        ],
        "summary": [{"participant": "Alice"}, {"participant": "Bob"}, {"participant": "Carol"}],
    }
    si = {"title": "Test", "iam": "I am", "all": "All", "cta_q": "Split?", "cta": "Go"}
    html = handler._render_share_page(result, "2026-04-04T00:00:00Z", si)
    assert "Bob" in html and "Alice" in html and "Carol" in html
    assert 'style="--i:0"' in html
    assert 'style="--i:1"' in html
    assert "TWD 300" in html
    assert "TWD 200" in html


def test_render_share_page_no_xss_via_participant_name():
    """SECURITY regression: a malicious participant name must not break out of
    the onclick JS string. Names should only appear inside HTML-escaped
    attribute contexts, never inline JS."""
    malicious = "');alert(1);//"
    result = {
        "currency": "TWD", "total_expenses": 100,
        "settlements": [{"from": malicious, "to": "Alice", "amount": 100}],
        "summary": [{"participant": malicious}, {"participant": "Alice"}],
    }
    si = {"title": "T", "cta_q": "?", "cta": "Go"}
    html = handler._render_share_page(result, "", si)
    # No inline onclick=filterMe(...) on rendered buttons — event delegation only
    assert "onclick=\"filterMe" not in html
    # Raw quote must not appear inside any attribute as plain text
    assert "alert(1)" not in html or "&#x27;" in html  # if present, must be escaped
    # Specifically, the exploit string must be HTML-encoded everywhere
    assert "');alert(1);//" not in html


def test_host_header_rejects_execute_api(monkeypatch):
    """SECURITY: when ALLOWED_HOSTS is set, hits on the raw execute-api.amazonaws.com
    endpoint must be rejected with 403 so Cloudflare/WAF in front of the custom
    domain cannot be bypassed."""
    monkeypatch.setenv("ALLOWED_HOSTS", "split.redarch.dev")
    resp = lambda_handler({
        "rawPath": "/health",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {"host": "aztyjlixm1.execute-api.ap-northeast-1.amazonaws.com"},
    }, {})
    assert resp["statusCode"] == 403


def test_host_header_allows_custom_domain(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "split.redarch.dev")
    resp = lambda_handler({
        "rawPath": "/health",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {"host": "split.redarch.dev"},
    }, {})
    assert resp["statusCode"] == 200


def test_share_body_size_limit(share_env):
    """SECURITY: /v1/share must reject oversized payloads to protect DynamoDB."""
    huge = {"currency": "TWD", "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": 1, "split_among": ["A", "B"]}],
            "padding": "x" * 70000}
    resp = lambda_handler({
        "rawPath": "/v1/share",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {},
        "body": json.dumps(huge),
    }, {})
    assert resp["statusCode"] == 413


def test_404_page_has_g_color_scheme():
    """404 page should use G color scheme."""
    event = {"rawPath": "/s/nonexist-404", "requestContext": {"http": {"method": "GET"}}, "headers": {}}
    # Need share_env for _get_share mock, but without it we get an error
    # Test the NOT_FOUND_HTML constant directly
    assert "#d5d0c8" in handler.NOT_FOUND_HTML
    assert "#2d4a4a" in handler.NOT_FOUND_HTML or "#e8a84c" in handler.NOT_FOUND_HTML


# ---------------------------------------------------------------------------
# Integration test: full flow
# ---------------------------------------------------------------------------

def test_full_flow_create_settle_share_view(groups_env, share_env, monkeypatch):
    """Integration: create group → split settle → share → view share page."""
    monkeypatch.delenv("SECRET_ARN", raising=False)

    # Step 1: Create group (no wallets)
    group_body = {
        "group_id": "integration-test",
        "participants": [{"name": "Alice"}, {"name": "Bob"}, {"name": "Carol"}],
    }
    resp1 = lambda_handler(_groups_event(group_body), {})
    assert resp1["statusCode"] == 200
    assert json.loads(resp1["body"])["participants"] == 3

    # Step 2: Split settle with group_id (no execution since no wallets)
    settle_body = {
        "currency": "TWD", "group_id": "integration-test",
        "participants": ["Alice", "Bob", "Carol"],
        "expenses": [
            {"description": "Dinner", "paid_by": "Alice", "amount": 1200, "split_among": ["Alice", "Bob", "Carol"]},
            {"description": "Taxi", "paid_by": "Bob", "amount": 300, "split_among": ["Alice", "Bob", "Carol"]},
        ],
    }
    resp2 = lambda_handler({
        "rawPath": "/v1/split_settle", "requestContext": {"http": {"method": "POST"}},
        "headers": {"x-api-key": "testkey"}, "body": json.dumps(settle_body),
    }, {})
    assert resp2["statusCode"] == 200
    result = json.loads(resp2["body"])
    assert result["num_settlements"] == 2
    assert result["total_expenses"] == 1500
    assert "execution" not in result  # no wallets

    # Step 3: Share with lang
    share_body = {
        "currency": "TWD", "participants": ["Alice", "Bob", "Carol"], "lang": "zh-TW",
        "expenses": settle_body["expenses"],
    }
    resp3 = lambda_handler({
        "rawPath": "/v1/share", "requestContext": {"http": {"method": "POST"}},
        "headers": {}, "body": json.dumps(share_body),
    }, {})
    assert resp3["statusCode"] == 200
    share_result = json.loads(resp3["body"])
    share_url = share_result["url"]
    assert "lang=zh-TW" in share_url
    share_id = share_result["share_id"]

    # Step 4: View share page
    resp4 = lambda_handler({
        "rawPath": f"/s/{share_id}", "requestContext": {"http": {"method": "GET"}},
        "headers": {}, "queryStringParameters": {"lang": "zh-TW"},
    }, {})
    assert resp4["statusCode"] == 200
    body = resp4["body"]
    assert "分帳仙貝" in body  # Chinese title
    assert "Alice" in body
    assert "TWD" in body
    assert "identity-card" in body  # new identity card layout


# ---------------------------------------------------------------------------
# Native app backend support tests
# ---------------------------------------------------------------------------

def test_apple_app_site_association(monkeypatch):
    """GET /.well-known/apple-app-site-association returns valid AASA JSON."""
    monkeypatch.setattr(handler, "_APPLE_APP_ID", "ABC123.com.splitsenpai.app")
    resp = lambda_handler({
        "rawPath": "/.well-known/apple-app-site-association",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {},
    }, {})
    assert resp["statusCode"] == 200
    assert resp["headers"]["Content-Type"] == "application/json"
    body = json.loads(resp["body"])
    assert "applinks" in body
    assert body["applinks"]["details"][0]["paths"] == ["/s/*"]


def test_aasa_returns_404_when_unconfigured(monkeypatch):
    """AASA returns 404 when APPLE_APP_ID env var is unset (no placeholder published)."""
    monkeypatch.setattr(handler, "_APPLE_APP_ID", "")
    resp = lambda_handler({
        "rawPath": "/.well-known/apple-app-site-association",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {},
    }, {})
    assert resp["statusCode"] == 404


def test_assetlinks_json(monkeypatch):
    """GET /.well-known/assetlinks.json returns valid Android asset links."""
    monkeypatch.setattr(handler, "_ANDROID_PACKAGE", "com.splitsenpai.app")
    monkeypatch.setattr(handler, "_ANDROID_SHA256", "AA:BB:CC:DD")
    resp = lambda_handler({
        "rawPath": "/.well-known/assetlinks.json",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {},
    }, {})
    assert resp["statusCode"] == 200
    assert resp["headers"]["Content-Type"] == "application/json"
    body = json.loads(resp["body"])
    assert isinstance(body, list)
    assert body[0]["target"]["namespace"] == "android_app"
    assert body[0]["target"]["package_name"] == "com.splitsenpai.app"


def test_assetlinks_returns_404_when_unconfigured(monkeypatch):
    """Assetlinks returns 404 when env vars unset (never publish placeholder SHA256)."""
    monkeypatch.setattr(handler, "_ANDROID_PACKAGE", "")
    monkeypatch.setattr(handler, "_ANDROID_SHA256", "")
    resp = lambda_handler({
        "rawPath": "/.well-known/assetlinks.json",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {},
    }, {})
    assert resp["statusCode"] == 404


def test_share_json_endpoint_returns_data(monkeypatch):
    """GET /v1/share/{id} returns share data as JSON."""
    fake_data = {
        "request_body": {"currency": "TWD", "participants": ["A", "B"],
                         "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "B"]}]},
        "result": {"currency": "TWD", "total_expenses": 100,
                   "settlements": [{"from": "B", "to": "A", "amount": 50}],
                   "summary": [{"participant": "A", "paid": 100, "owed": 50, "net": 50},
                               {"participant": "B", "paid": 0, "owed": 50, "net": -50}]},
        "created_at": "2026-04-04T00:00:00Z",
        "ttl_expiry": int(time.time()) + 86400,
    }
    monkeypatch.setattr(handler, "_get_share", lambda sid: fake_data if sid == "abc12345" else None)

    resp = lambda_handler({
        "rawPath": "/v1/share/abc12345",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {},
    }, {})
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["share_id"] == "abc12345"
    assert body["result"]["currency"] == "TWD"
    assert body["result"]["settlements"][0]["from"] == "B"
    assert body["created_at"] == "2026-04-04T00:00:00Z"


def test_share_json_endpoint_not_found(monkeypatch):
    """GET /v1/share/{id} returns 404 for unknown share."""
    monkeypatch.setattr(handler, "_get_share", lambda sid: None)

    resp = lambda_handler({
        "rawPath": "/v1/share/nonexistent",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {},
    }, {})
    assert resp["statusCode"] == 404
    body = json.loads(resp["body"])
    assert "not found" in body["error"].lower() or "expired" in body["error"].lower()


def test_share_json_endpoint_expired(monkeypatch):
    """GET /v1/share/{id} returns 404 for expired share."""
    fake_data = {
        "request_body": {},
        "result": {},
        "created_at": "2026-01-01T00:00:00Z",
        "ttl_expiry": int(time.time()) - 1,  # expired
    }
    monkeypatch.setattr(handler, "_get_share", lambda sid: fake_data)

    resp = lambda_handler({
        "rawPath": "/v1/share/expired123",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {},
    }, {})
    assert resp["statusCode"] == 404


def test_share_json_endpoint_no_id():
    """GET /v1/share/ with no ID returns 404."""
    resp = lambda_handler({
        "rawPath": "/v1/share/",
        "requestContext": {"http": {"method": "GET"}},
        "headers": {},
    }, {})
    assert resp["statusCode"] == 404


def test_share_page_has_smart_app_banner():
    """Share page HTML includes Smart App Banner meta tag."""
    from handler import SHARE_PAGE_TEMPLATE
    assert 'name="apple-itunes-app"' in SHARE_PAGE_TEMPLATE


def test_aasa_method_not_allowed():
    """POST to AASA endpoint returns 405."""
    resp = lambda_handler({
        "rawPath": "/.well-known/apple-app-site-association",
        "requestContext": {"http": {"method": "POST"}},
        "headers": {},
    }, {})
    assert resp["statusCode"] == 405


def test_share_page_has_identity_card():
    """New layout uses identity-card; me-picker is removed."""
    import handler
    result = {
        "currency": "NT",
        "total_expenses": 4500,
        "settlements": [
            {"from": "Bob", "to": "Alice", "amount": 1200},
            {"from": "Charlie", "to": "Alice", "amount": 600},
        ],
        "summary": [
            {"participant": "Alice"},
            {"participant": "Bob"},
            {"participant": "Charlie"},
        ],
    }
    html = handler._render_share_page(result, "2026-04-08T00:00:00Z",
                                       si=None, share_id="abc12345")
    assert "identity-card" in html
    assert "me-picker" not in html
    assert "me-btn" not in html
    assert "{{iam}}" not in html
    assert "{{me_buttons}}" not in html
    assert "{{all_label}}" not in html


def test_share_page_bootstrap_includes_settlements():
    """Client JS needs settlements + participants to compute owed/owes."""
    import handler
    import json
    result = {
        "currency": "NT",
        "total_expenses": 1000,
        "settlements": [{"from": "Bob", "to": "Alice", "amount": 1000}],
        "summary": [{"participant": "Alice"}, {"participant": "Bob"}],
    }
    html = handler._render_share_page(result, "2026-04-08T00:00:00Z",
                                       si=None, share_id="xyz99999")
    marker = "window.__SHARE = "
    start = html.index(marker) + len(marker)
    end = html.index(";</script>", start)
    payload = html[start:end]
    payload_clean = (payload.replace("\\u003c", "<")
                            .replace("\\u0026", "&")
                            .replace("\\u0027", "'"))
    data = json.loads(payload_clean)
    assert data["share_id"] == "xyz99999"
    assert data["participants"] == ["Alice", "Bob"]
    assert len(data["settlements"]) == 1
    assert data["settlements"][0]["from"] == "Bob"


def test_share_page_bill_details_rendered():
    """Bill details section is rendered when request_body has expenses."""
    result = {
        "currency": "NT",
        "total_expenses": 4500,
        "settlements": [
            {"from": "Bob", "to": "Alice", "amount": 1200},
        ],
        "summary": [
            {"participant": "Alice"},
            {"participant": "Bob"},
            {"participant": "Charlie"},
        ],
    }
    request_body = {
        "expenses": [
            {"description": "晚餐", "paid_by": "Alice", "amount": 2400,
             "split_among": ["Alice", "Bob", "Charlie"]},
            {"description": "飲料", "paid_by": "Alice", "amount": 600,
             "split_among": ["Bob", "Charlie"]},
            {"description": "計程車", "paid_by": "Bob", "amount": 1500,
             "split_among": ["Alice", "Bob", "Charlie"]},
        ],
    }
    html = handler._render_share_page(result, "2026-04-08T00:00:00Z",
                                       si=None, share_id="bill123",
                                       request_body=request_body)
    assert "bill-details" in html
    assert "帳單明細" in html
    assert "（3 筆）" in html
    assert "晚餐" in html
    assert "飲料" in html
    assert "計程車" in html
    assert "Alice 付" in html
    assert "Bob 付" in html
    assert "分給 Alice, Bob, Charlie" in html


def test_share_page_no_bill_details_without_expenses():
    """No bill details <details> element when request_body has no expenses."""
    result = {
        "currency": "NT",
        "total_expenses": 1000,
        "settlements": [{"from": "Bob", "to": "Alice", "amount": 1000}],
        "summary": [{"participant": "Alice"}, {"participant": "Bob"}],
    }
    html = handler._render_share_page(result, "2026-04-08T00:00:00Z",
                                       si=None, share_id="nobill1")
    # CSS class name will appear in <style>, but no <details> element
    assert '<details class="bill-details">' not in html
    assert "帳單明細" not in html


def test_share_page_bill_details_xss():
    """Bill details escapes user input (description, paid_by, split_among)."""
    result = {
        "currency": "NT",
        "total_expenses": 100,
        "settlements": [],
        "summary": [{"participant": "<script>alert(1)</script>"}],
    }
    request_body = {
        "expenses": [
            {"description": "<img onerror=alert(1)>", "paid_by": "<b>evil</b>",
             "amount": 100, "split_among": ["<script>alert(1)</script>"]},
        ],
    }
    html = handler._render_share_page(result, "2026-04-08T00:00:00Z",
                                       si=None, share_id="xss123",
                                       request_body=request_body)
    # Extract just the bill-details section
    start = html.index('<details class="bill-details">')
    end = html.index('</details>', start) + len('</details>')
    bill_section = html[start:end]
    assert "<script>" not in bill_section
    assert "<img " not in bill_section
    assert "<b>" not in bill_section
    assert "&lt;img" in bill_section
    assert "&lt;b&gt;" in bill_section
