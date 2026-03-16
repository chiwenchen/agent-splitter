import json
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/split_settle"))

import handler
from handler import split_settle, lambda_handler


@pytest.fixture(autouse=True)
def reset_caches(monkeypatch):
    handler._cached_api_key = None
    handler._cached_alchemy_url = None
    yield
    handler._cached_api_key = None
    handler._cached_alchemy_url = None


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
    assert "/split_settle" in schema["paths"]


def test_health_endpoint():
    event = {"rawPath": "/health"}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["status"] == "ok"
