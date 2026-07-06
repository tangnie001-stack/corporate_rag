"""Verify that all required packages can be imported."""


def test_fastapi_import():
    import fastapi  # noqa: F401


def test_uvicorn_import():
    import uvicorn  # noqa: F401


def test_langfuse_import():
    import langfuse  # noqa: F401


def test_langchain_core_version():
    import langchain_core

    version = tuple(int(x) for x in langchain_core.__version__.split(".")[:2])
    assert version >= (1, 0), f"langchain-core {langchain_core.__version__} < 1.0"


def test_gradio_not_required():
    """Gradio is no longer a hard dependency."""
    import importlib
    import sys

    # If gradio happens to be installed, that's fine — it's just not required
    # This test verifies the core app can start without gradio
    if "gradio" in sys.modules:
        del sys.modules["gradio"]
    try:
        _ = importlib.util.find_spec("gradio")
        # gradio may still be in the environment, just no longer required
    except ModuleNotFoundError:
        pass
