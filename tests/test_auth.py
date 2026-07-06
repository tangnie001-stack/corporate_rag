# tests/test_auth.py
import pytest
from src.infra.auth.user_auth import UserAuth
from unittest.mock import Mock

def test_hash_consistent():
    assert UserAuth.hash_password("abc") == UserAuth.hash_password("abc")

def test_verify_correct():
    h = UserAuth.hash_password("correct")
    assert UserAuth.verify_password("correct", h)

def test_verify_wrong():
    h = UserAuth.hash_password("correct")
    assert not UserAuth.verify_password("wrong", h)

def test_token_format():
    t = UserAuth.generate_token()
    assert len(t.split("-")) == 5

def test_store_and_retrieve():
    r = Mock()
    r.get.return_value = b"uid"
    UserAuth.store_token(r, "tok", "uid")
    assert UserAuth.get_user_id_from_token(r, "tok") == "uid"

def test_invalid_token():
    r = Mock()
    r.get.return_value = None
    assert UserAuth.get_user_id_from_token(r, "bad") is None
