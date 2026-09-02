from src.agents.tools.registry import ToolRegistry


def test_register_and_enabled():
    reg = ToolRegistry()
    reg.register("a", lambda: 1)
    reg.register("b", lambda: 2, enabled=False)
    assert len(reg.enabled_tools()) == 1


def test_toggle_enable():
    reg = ToolRegistry()
    reg.register("a", lambda: 1)
    reg.set_enabled("a", False)
    assert reg.enabled_tools() == []
    reg.set_enabled("a", True)
    assert len(reg.enabled_tools()) == 1


def test_get_missing_raises():
    reg = ToolRegistry()
    try:
        reg.get("nope")
    except KeyError:
        pass
    else:
        raise AssertionError("should raise KeyError")
