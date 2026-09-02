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


def test_unregister_removes_entry():
    reg = ToolRegistry()
    reg.register("a", lambda: 1)
    reg.unregister("a")
    assert reg.enabled_tools() == []


def test_unregister_missing_silent():
    reg = ToolRegistry()
    reg.unregister("nope")  # 不存在应静默，不抛异常


def test_set_enabled_missing_silent():
    reg = ToolRegistry()
    reg.set_enabled("nope", False)  # 不存在应静默，不抛异常
