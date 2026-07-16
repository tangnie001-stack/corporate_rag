def test_response_processor_exists():
    from src.middleware.response_processor import response_processor_middleware

    assert callable(response_processor_middleware)


def test_auth_middleware_exists():
    from src.middleware.auth import auth_middleware

    assert callable(auth_middleware)
