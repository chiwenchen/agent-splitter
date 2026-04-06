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
