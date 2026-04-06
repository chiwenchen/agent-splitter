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
