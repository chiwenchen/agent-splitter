import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/split_settle"))

import handler
from handler import split_settle, lambda_handler


@pytest.fixture(autouse=True)
def reset_api_key_cache():
    handler._cached_api_key = None
    yield
    handler._cached_api_key = None


# --- Core logic tests ---

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

    # Carol owes the most, so she pays Alice first
    settlements = result["settlements"]
    assert any(s["from"] == "Carol" and s["to"] == "Alice" and s["amount"] == 500 for s in settlements)
    assert any(s["from"] == "Bob" and s["to"] == "Alice" and s["amount"] == 200 for s in settlements)


def test_two_people_even_split():
    result = split_settle({
        "currency": "USD",
        "participants": ["A", "B"],
        "expenses": [
            {"paid_by": "A", "amount": 100, "split_among": ["A", "B"]},
        ],
    })
    assert result["num_settlements"] == 1
    assert result["settlements"][0] == {"from": "B", "to": "A", "amount": 50.0}


def test_remainder_distribution():
    # 100 cents split among 3 = 33, 34, 33 — sum must be 100
    result = split_settle({
        "currency": "TWD",
        "participants": ["A", "B", "C"],
        "expenses": [
            {"paid_by": "A", "amount": 1.00, "split_among": ["A", "B", "C"]},
        ],
    })
    total_owed = sum(s["total_owed"] for s in result["summary"])
    assert round(total_owed * 100) == 100


def test_already_settled():
    # Each person pays exactly their share — no settlements needed
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
    # A pays for B and C only — A is owed the full amount
    result = split_settle({
        "currency": "TWD",
        "participants": ["A", "B", "C"],
        "expenses": [
            {"paid_by": "A", "amount": 300, "split_among": ["B", "C"]},
        ],
    })
    by_person = {s["participant"]: s for s in result["summary"]}
    assert by_person["A"]["balance"] == 300
    assert by_person["B"]["balance"] == -150
    assert by_person["C"]["balance"] == -150


# --- Validation tests ---

def test_missing_currency():
    try:
        split_settle({"participants": ["A", "B"], "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "B"]}]})
        assert False
    except ValueError as e:
        assert "currency" in str(e)


def test_too_few_participants():
    try:
        split_settle({"currency": "TWD", "participants": ["A"], "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A"]}]})
        assert False
    except ValueError:
        pass


def test_too_many_participants():
    participants = [str(i) for i in range(21)]
    try:
        split_settle({"currency": "TWD", "participants": participants, "expenses": [{"paid_by": "0", "amount": 100, "split_among": ["0", "1"]}]})
        assert False
    except ValueError as e:
        assert "20" in str(e)


def test_paid_by_not_in_participants():
    try:
        split_settle({
            "currency": "TWD",
            "participants": ["A", "B"],
            "expenses": [{"paid_by": "X", "amount": 100, "split_among": ["A", "B"]}],
        })
        assert False
    except ValueError as e:
        assert "paid_by" in str(e)


def test_split_among_not_in_participants():
    try:
        split_settle({
            "currency": "TWD",
            "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "X"]}],
        })
        assert False
    except ValueError as e:
        assert "not in participants" in str(e)


def test_zero_amount():
    try:
        split_settle({
            "currency": "TWD",
            "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": 0, "split_among": ["A", "B"]}],
        })
        assert False
    except ValueError:
        pass


# --- Lambda handler tests ---

def test_lambda_handler_success():
    event = {
        "body": json.dumps({
            "currency": "TWD",
            "participants": ["Alice", "Bob"],
            "expenses": [{"paid_by": "Alice", "amount": 200, "split_among": ["Alice", "Bob"]}],
        })
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["num_settlements"] == 1


def test_lambda_handler_bad_request():
    event = {"body": json.dumps({"currency": "TWD"})}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 400
    assert "error" in json.loads(response["body"])


def test_lambda_handler_empty_body():
    event = {"body": None}
    response = lambda_handler(event, {})
    assert response["statusCode"] == 400


# --- API Key tests ---

def test_api_key_accepted(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret123")
    event = {
        "rawPath": "/split_settle",
        "headers": {"x-api-key": "secret123"},
        "body": json.dumps({
            "currency": "TWD",
            "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "B"]}],
        }),
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200


def test_api_key_rejected(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret123")
    event = {
        "rawPath": "/split_settle",
        "headers": {"x-api-key": "wrong"},
        "body": json.dumps({
            "currency": "TWD",
            "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "B"]}],
        }),
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 403


def test_api_key_missing_header(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret123")
    event = {
        "rawPath": "/split_settle",
        "headers": {},
        "body": json.dumps({
            "currency": "TWD",
            "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "B"]}],
        }),
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 403


def test_api_key_disabled_when_env_empty(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SECRET_ARN", raising=False)
    event = {
        "rawPath": "/split_settle",
        "body": json.dumps({
            "currency": "TWD",
            "participants": ["A", "B"],
            "expenses": [{"paid_by": "A", "amount": 100, "split_among": ["A", "B"]}],
        }),
    }
    response = lambda_handler(event, {})
    assert response["statusCode"] == 200


# --- OpenAPI schema test ---

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
    body = json.loads(response["body"])
    assert body["status"] == "ok"
