import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/split_settle"))

import handler


def test_verify_jwt_returns_none_when_team_domain_unset(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    assert handler._verify_access_jwt("a.b.c") is None


def test_verify_jwt_returns_none_when_aud_unset(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "")
    assert handler._verify_access_jwt("a.b.c") is None


def test_verify_jwt_returns_none_for_malformed_token(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    assert handler._verify_access_jwt("not-a-jwt") is None


def test_verify_jwt_returns_none_for_two_part_token(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    assert handler._verify_access_jwt("only.two") is None


def test_admin_auth_returns_503_when_unconfigured(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "")
    event = {"rawPath": "/admin", "headers": {}, "requestContext": {"http": {"method": "GET", "path": "/admin"}}}
    response = handler.lambda_handler(event, {})
    assert response["statusCode"] == 503


def test_admin_auth_returns_401_without_jwt_header(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    event = {"rawPath": "/admin", "headers": {}, "requestContext": {"http": {"method": "GET", "path": "/admin"}}}
    response = handler.lambda_handler(event, {})
    assert response["statusCode"] == 401


def test_admin_auth_returns_401_with_invalid_jwt(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    event = {
        "rawPath": "/admin",
        "headers": {"cf-access-jwt-assertion": "invalid.jwt.token"},
        "requestContext": {"http": {"method": "GET", "path": "/admin"}},
    }
    response = handler.lambda_handler(event, {})
    assert response["statusCode"] == 401


def test_admin_auth_returns_403_when_wrong_email(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    monkeypatch.setenv("CF_ALLOWED_EMAIL", "owner@example.com")
    monkeypatch.setattr(handler, "_verify_access_jwt", lambda t: {"email": "stranger@example.com"})
    event = {
        "rawPath": "/admin",
        "headers": {"cf-access-jwt-assertion": "valid.jwt"},
        "requestContext": {"http": {"method": "GET", "path": "/admin"}},
    }
    response = handler.lambda_handler(event, {})
    assert response["statusCode"] == 403


def test_admin_stats_aggregates_shares(monkeypatch):
    import json as _json
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "test.cloudflareaccess.com")
    monkeypatch.setenv("CF_ACCESS_AUD", "fake-aud")
    monkeypatch.setenv("CF_ALLOWED_EMAIL", "owner@example.com")
    monkeypatch.setattr(handler, "_verify_access_jwt", lambda t: {"email": "owner@example.com"})

    fake_items = [
        {
            "PK": {"S": "SHARE#1"},
            "request_body": {"S": '{"currency":"TWD"}'},
            "result": {"S": '{"currency":"TWD","total_expenses":1500}'},
            "created_at": {"S": "2026-04-06T10:00:00Z"},
        },
        {
            "PK": {"S": "SHARE#2"},
            "request_body": {"S": '{"currency":"TWD"}'},
            "result": {"S": '{"currency":"TWD","total_expenses":500}'},
            "created_at": {"S": "2026-04-06T11:00:00Z"},
        },
        {
            "PK": {"S": "SHARE#3"},
            "request_body": {"S": '{"currency":"USD"}'},
            "result": {"S": '{"currency":"USD","total_expenses":50}'},
            "created_at": {"S": "2026-04-05T10:00:00Z"},
        },
    ]

    monkeypatch.setattr(handler, "_scan_all_shares", lambda: fake_items)

    event = {
        "rawPath": "/admin/api/stats",
        "headers": {"cf-access-jwt-assertion": "valid"},
        "requestContext": {"http": {"method": "GET", "path": "/admin/api/stats"}},
    }
    response = handler.lambda_handler(event, {})
    assert response["statusCode"] == 200
    body = _json.loads(response["body"])
    assert body["total_shares"] == 3
    assert body["currency_breakdown"]["TWD"] == 2
    assert body["currency_breakdown"]["USD"] == 1
    assert body["avg_amount_by_currency"]["TWD"] == 1000.0
    assert body["avg_amount_by_currency"]["USD"] == 50.0
    days = {d["date"]: d["count"] for d in body["shares_by_day"]}
    assert days["2026-04-06"] == 2
    assert days["2026-04-05"] == 1
