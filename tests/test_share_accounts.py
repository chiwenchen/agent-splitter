"""Tests for share accounts API (/v1/share/{id}/accounts)."""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/split_settle"))

import handler  # noqa: E402


# In-memory fakes mirroring the _save_share / _get_share pattern used in
# tests/test_handler.py. moto is not available in this repo, so we mock the
# DynamoDB helpers directly instead of spinning up a fake table.
_fake_shares = {}
_fake_accounts = {}  # { share_id: { participant: {"text": str, "updated_by": str} } }


def _fake_share_save(share_id, request_body, result):
    _fake_shares[share_id] = {
        "request_body": request_body,
        "result": result,
        "created_at": "2026-04-08T00:00:00Z",
        "ttl_expiry": int(time.time()) + 86400 * 30,
    }


def _fake_share_get(share_id):
    return _fake_shares.get(share_id)


def _fake_get_accounts(share_id):
    rows = _fake_accounts.get(share_id, {})
    return {name: row["text"] for name, row in rows.items()}


def _fake_save_account(share_id, participant, account_text, device_id, ttl_expiry):
    _fake_accounts.setdefault(share_id, {})[participant] = {
        "text": account_text,
        "updated_by": device_id or "",
        "ttl_expiry": ttl_expiry,
    }


def _fake_delete_account(share_id, participant):
    if share_id in _fake_accounts:
        _fake_accounts[share_id].pop(participant, None)


@pytest.fixture
def ddb(monkeypatch):
    _fake_shares.clear()
    _fake_accounts.clear()
    monkeypatch.setenv("GROUPS_TABLE", "test-groups")
    monkeypatch.setattr(handler, "_save_share", _fake_share_save)
    monkeypatch.setattr(handler, "_get_share", _fake_share_get)
    # Only patch account helpers if they exist yet (Tasks 2/3/5 add them).
    for name, fn in [
        ("_get_accounts", _fake_get_accounts),
        ("_save_account", _fake_save_account),
        ("_delete_account", _fake_delete_account),
    ]:
        if hasattr(handler, name):
            monkeypatch.setattr(handler, name, fn)
    yield


def _seed_share(share_id="abc12345", participants=("Alice", "Bob")):
    _fake_share_save(
        share_id,
        {"participants": list(participants), "expenses": []},
        {"transfers": []},
    )


def _invoke(method, path, body=None, headers=None):
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": headers or {},
        "body": json.dumps(body) if body is not None else None,
    }
    return handler.lambda_handler(event, None)


def test_get_accounts_empty(ddb):
    _seed_share()
    resp = _invoke(
        "GET",
        "/v1/share/abc12345/accounts",
        headers={"host": "split.redarch.dev"},
    )
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {}


def test_put_then_get_account(ddb):
    _seed_share()
    put = _invoke(
        "PUT",
        "/v1/share/abc12345/accounts/Alice",
        body={"account_text": "國泰 700-12345678"},
        headers={"x-device-id": "dev-1"},
    )
    assert put["statusCode"] == 200
    assert json.loads(put["body"]) == {"ok": True}

    get = _invoke("GET", "/v1/share/abc12345/accounts")
    assert get["statusCode"] == 200
    assert json.loads(get["body"]) == {"Alice": "國泰 700-12345678"}


def test_put_unknown_participant(ddb):
    _seed_share()
    resp = _invoke(
        "PUT",
        "/v1/share/abc12345/accounts/Charlie",
        body={"account_text": "x"},
        headers={"x-device-id": "d"},
    )
    assert resp["statusCode"] == 400


def test_put_too_long(ddb):
    _seed_share()
    resp = _invoke(
        "PUT",
        "/v1/share/abc12345/accounts/Alice",
        body={"account_text": "x" * 501},
        headers={"x-device-id": "d"},
    )
    assert resp["statusCode"] == 400


def test_put_missing_share(ddb):
    resp = _invoke(
        "PUT",
        "/v1/share/nope0000/accounts/Alice",
        body={"account_text": "x"},
        headers={"x-device-id": "d"},
    )
    assert resp["statusCode"] == 404


def test_put_expired_share(ddb, monkeypatch):
    _seed_share()
    real_time = time.time
    monkeypatch.setattr(
        handler.time, "time", lambda: real_time() + 86400 * 31
    )
    resp = _invoke(
        "PUT",
        "/v1/share/abc12345/accounts/Alice",
        body={"account_text": "x"},
        headers={"x-device-id": "d"},
    )
    assert resp["statusCode"] == 404
