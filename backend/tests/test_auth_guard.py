"""Test auth_guard — token aal1 (cuma password) ditolak, aal2 (lolos MFA) diterima."""
import base64
import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
import auth_guard


def _token(aal):
    payload = base64.urlsafe_b64encode(json.dumps({"aal": aal}).encode()).decode().rstrip("=")
    return f"header.{payload}.sig"


@pytest.fixture(autouse=True)
def fake_supabase(monkeypatch):
    fake = SimpleNamespace(auth=SimpleNamespace(get_user=lambda t: SimpleNamespace(user=object())))
    monkeypatch.setattr(auth_guard, "supabase", fake)


def test_aal2_accepted():
    auth_guard.require_auth(authorization=f"Bearer {_token('aal2')}", x_service_key=None)


def test_aal1_rejected():
    with pytest.raises(HTTPException) as e:
        auth_guard.require_auth(authorization=f"Bearer {_token('aal1')}", x_service_key=None)
    assert "MFA" in e.value.detail


def test_garbage_token_rejected():
    with pytest.raises(HTTPException):
        auth_guard.require_auth(authorization="Bearer nope", x_service_key=None)
