import pytest
from starlette.middleware.base import BaseHTTPMiddleware
from src.middleware.response_envelope import ResponseEnvelopeMiddleware


def test_response_envelope_is_middleware():
    assert issubclass(ResponseEnvelopeMiddleware, BaseHTTPMiddleware)


def test_auth_middleware_exists():
    from src.middleware.auth import auth_middleware

    assert callable(auth_middleware)
