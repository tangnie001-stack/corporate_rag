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


def test_tool_entry_readonly_defaults_true():
    """未显式声明时 readonly 默认 True（现有工具均只读）。"""
    from src.agents.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("retrieve_kb", lambda: None)

    assert registry.readonly_map() == {"retrieve_kb": True}


def test_tool_entry_readonly_explicit_false():
    """写类工具显式声明 readonly=False。"""
    from src.agents.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("publish_report", lambda: None, readonly=False)

    assert registry.readonly_map() == {"publish_report": False}


def test_register_declares_readonly_into_single_source():
    """注册时把只读性写入进程级声明表（供 skill 加载侧读取）。"""
    from src.agents.tools.readonly import readonly_map
    from src.agents.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("publish_report", lambda: None, readonly=False)

    assert readonly_map()["publish_report"] is False
