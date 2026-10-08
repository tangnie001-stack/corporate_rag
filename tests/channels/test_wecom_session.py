"""会话/用户标识派生：36 字符、确定性、跨进程稳定（design D2）。"""

import pathlib
import subprocess
import sys

from src.channels.wecom.session import (
    build_session_title,
    derive_session_id,
    derive_user_id,
)


def test_session_id_is_36_chars_and_stable():
    first = derive_session_id(
        bot_key="dev", chattype="group", chatid="CHAT1", from_userid="U1"
    )
    second = derive_session_id(
        bot_key="dev", chattype="group", chatid="CHAT1", from_userid="U2"
    )
    assert len(first) == 36
    assert first == second  # 同群：不同成员得到同一会话


def test_single_chat_keys_on_userid():
    a = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    b = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U2"
    )
    c = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    assert a == c
    assert a != b  # 单聊：按人区分


def test_bot_key_participates_in_session_id():
    a = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    b = derive_session_id(
        bot_key="support", chattype="single", chatid=None, from_userid="U1"
    )
    assert a != b  # 三台机器人同一用户不共会话


def test_user_id_is_36_chars_and_deterministic():
    assert len(derive_user_id("U1")) == 36
    assert derive_user_id("U1") == derive_user_id("U1")
    assert derive_user_id("U1") != derive_user_id("U2")


def test_session_id_and_user_id_differ_for_same_identifier():
    session_id = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    assert session_id != derive_user_id("U1")


def test_user_id_is_pinned_by_literal_expected_value():
    """钉住 `wecom-user|{from_userid}` 前缀：改掉它会静默重键全部用户行。"""
    assert derive_user_id("U1") == "6333fb86-144e-5486-853c-7a7bc7c65c3a"


def test_title_is_readable_and_bounded():
    title = build_session_title(
        bot_key="finance", chattype="group", chatid="CHAT9", from_userid="U9"
    )
    assert "finance" in title
    assert "CHAT9" in title
    assert len(title) <= 256  # sessions.title 列宽


def test_title_is_truncated_to_column_width():
    """超长外部 id 不得撑爆 `sessions.title`（String(256)）。"""
    title = build_session_title(
        bot_key="dev", chattype="group", chatid="C" * 300, from_userid="U1"
    )
    assert len(title) == 256


def test_namespace_is_cross_process_stable():
    """WECOM_NS 必须是固定字面常量：换进程算出的 session_id 必须一致。"""
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    code = (
        "from src.channels.wecom.session import derive_session_id;"
        "print(derive_session_id(bot_key='dev', chattype='single',"
        " chatid=None, from_userid='U1'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=repo_root,
    )
    expected = derive_session_id(
        bot_key="dev", chattype="single", chatid=None, from_userid="U1"
    )
    assert out.stdout.strip() == expected
