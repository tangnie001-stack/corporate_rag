#!/usr/bin/env python3
"""LiteLLM Proxy Fallback 自动化测试。

测试场景：
  Case 1 — 正常路径：qwen3.7-max 直接响应，建立耗时基线
  Case 2 — Fallback 路径：qwen3.7-max 故障后自动切到 deepseek-v4-flash
  Case 3 — 恢复验证：恢复配置后 qwen3.7-max 恢复正常

用法：
  python tests/test_litellm_fallback.py

依赖：
  - httpx（langchain-openai 的传递依赖，通常已安装）
  - python-dotenv
  - docker compose
"""

import os
import sys
import time
import shutil
import subprocess
from pathlib import Path

import httpx
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_DIR / "litellm"
CONFIG_YAML = CONFIG_DIR / "config.yaml"
CONFIG_TEST = CONFIG_DIR / "config.test.yaml"
CONFIG_BAK = CONFIG_DIR / "config.yaml.bak"

PROXY_URL = "http://localhost:4000"
POLL_INTERVAL = 2
PROXY_STARTUP_TIMEOUT = 30
REQUEST_TIMEOUT = 60

# 运行时由 main() 赋值
MASTER_KEY: str = ""


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _load_master_key() -> str:
    """从 .env 加载 LITELLM_MASTER_KEY。"""
    env_path = PROJECT_DIR / ".env"
    if env_path.exists():
        load_dotenv(str(env_path))
    key = os.getenv("LITELLM_MASTER_KEY", "")
    if not key:
        print("❌ LITELLM_MASTER_KEY 未在 .env 中设置")
        sys.exit(1)
    return key


def _docker_compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """在项目目录下运行 docker compose 命令。"""
    cmd = ["docker", "compose"] + list(args)
    return subprocess.run(
        cmd,
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        check=check,
    )


def _wait_for_proxy(timeout: int = PROXY_STARTUP_TIMEOUT) -> None:
    """轮询 /models 端点直到 proxy 就绪。"""
    headers = {"Authorization": f"Bearer {MASTER_KEY}"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(
                f"{PROXY_URL}/models",
                headers=headers,
                timeout=5,
            )
            if resp.status_code == 200:
                print("   ✅ Proxy 就绪")
                return
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        time.sleep(POLL_INTERVAL)
    print(f"❌ Proxy 在 {timeout}s 内未就绪，退出")
    sys.exit(1)


def _restart_proxy() -> None:
    """重启 litellm-proxy 容器并等待就绪。"""
    print("   🔄 重启 litellm-proxy...")
    _docker_compose("up", "-d", "--force-recreate", "litellm-proxy")
    _wait_for_proxy()


def _chat_completion(model: str) -> httpx.Response:
    """向 proxy 发送聊天补全请求。"""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "你好，请用一句话介绍你自己。"}],
        "max_tokens": 100,
        "temperature": 0.1,
    }
    headers = {"Authorization": f"Bearer {MASTER_KEY}"}
    return httpx.post(
        f"{PROXY_URL}/chat/completions",
        json=payload,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )


def _check_env() -> None:
    """前置检查：文件是否存在、docker compose 是否可用。"""
    for f in [CONFIG_YAML, CONFIG_TEST]:
        if not f.exists():
            print(f"❌ 文件不存在: {f}")
            sys.exit(1)

    try:
        _docker_compose("ps", check=False)
    except FileNotFoundError:
        print("❌ docker compose 命令不可用")
        sys.exit(1)

    # 如果上次运行异常退出留下了备份，先恢复
    if CONFIG_BAK.exists():
        print("⚠️  发现上次残留的备份，正在恢复...")
        shutil.copy2(str(CONFIG_BAK), str(CONFIG_YAML))
        CONFIG_BAK.unlink(missing_ok=True)
        _restart_proxy()


def _run_case_normal(label: str) -> dict:
    """运行正常路径测试，返回结果字典。"""
    print(f"\n{'=' * 50}")
    print(f"Case: {label}")
    print(f"{'=' * 50}")

    t0 = time.time()
    resp = _chat_completion("qwen3.7-max")
    elapsed = time.time() - t0

    model_id = resp.headers.get("x-litellm-model-id", "")
    status = resp.status_code
    passed = status == 200 and model_id == "qwen3.7-max"

    content = ""
    if status == 200:
        body = resp.json()
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")

    print(f"   {'✅' if passed else '❌'} 状态码: {status}")
    print(f"   {'✅' if passed else '❌'} 模型: {model_id}")
    print(f"   {'✅' if passed else '❌'} 耗时: {elapsed:.2f}s")

    return {
        "passed": passed,
        "status_code": status,
        "model_id": model_id,
        "elapsed": elapsed,
        "content": content,
    }


def _run_case_fallback() -> dict:
    """运行 fallback 路径测试，返回结果字典。"""
    print(f"\n{'=' * 50}")
    print("Case: Fallback 路径 (qwen3.7-max → deepseek-v4-flash)")
    print(f"{'=' * 50}")

    t0 = time.time()
    resp = _chat_completion("qwen3.7-max")
    elapsed = time.time() - t0

    model_id = resp.headers.get("x-litellm-model-id", "")
    status = resp.status_code
    passed = status == 200 and model_id == "deepseek-v4-flash"

    content = ""
    if status == 200:
        body = resp.json()
        content = body.get("choices", [{}])[0].get("message", {}).get("content", "")

    if passed:
        print(f"   ✅ 状态码: {status}")
        print(f"   ✅ Fallback 成功，实际模型: {model_id}")
        print(f"   ✅ 耗时: {elapsed:.2f}s")
    elif status == 200:
        print(f"   ⚠️  状态码 200 但模型是 {model_id}，可能未触发 fallback")
    else:
        print(f"   ❌ 状态码: {status} - {resp.text[:200]}")

    return {
        "passed": passed,
        "status_code": status,
        "model_id": model_id,
        "elapsed": elapsed,
        "content": content,
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> None:
    global MASTER_KEY
    MASTER_KEY = _load_master_key()
    _check_env()

    results: dict[str, dict] = {}

    # ---- Phase 1: 正常路径（当前运行的 proxy，不动配置） ----
    print("\n" + "=" * 60)
    print("Phase 1: 正常路径测试")
    print("=" * 60)
    results["normal"] = _run_case_normal("正常路径基线 (qwen3.7-max)")

    # ---- Phase 2: Fallback 测试 ----
    print("\n" + "=" * 60)
    print("Phase 2: Fallback 测试")
    print("=" * 60)
    try:
        print("   📦 备份 config.yaml...")
        shutil.copy2(str(CONFIG_YAML), str(CONFIG_BAK))

        print("   📝 写入测试配置 (qwen3.7-max key 错误)...")
        shutil.copy2(str(CONFIG_TEST), str(CONFIG_YAML))

        _restart_proxy()
        results["fallback"] = _run_case_fallback()

    finally:
        # ---- Phase 3: 恢复配置并验证 ----
        if CONFIG_BAK.exists():
            print(f"\n{'=' * 60}")
            print("Phase 3: 恢复配置并验证")
            print(f"{'=' * 60}")
            print("   📦 恢复原始 config.yaml...")
            shutil.copy2(str(CONFIG_BAK), str(CONFIG_YAML))
            CONFIG_BAK.unlink(missing_ok=True)

            _restart_proxy()
            results["restore"] = _run_case_normal("恢复验证 (qwen3.7-max)")

    # ---- 报告 ----
    print(f"\n{'=' * 60}")
    print("📊 测试报告")
    print(f"{'=' * 60}")

    normal = results.get("normal", {})
    fallback = results.get("fallback", {})
    restore = results.get("restore", {})

    if normal:
        print(
            f"  Case 1 (正常路径):     {'✅' if normal['passed'] else '❌'}  "
            f"{normal['elapsed']:.2f}s"
        )
    if fallback:
        delay = fallback["elapsed"] - normal.get("elapsed", 0)
        print(
            f"  Case 2 (Fallback 切换): {'✅' if fallback['passed'] else '❌'}  "
            f"{fallback['elapsed']:.2f}s  (切换延迟: {delay:.2f}s)"
        )
    if restore:
        print(
            f"  Case 3 (恢复后验证):   {'✅' if restore['passed'] else '❌'}  "
            f"{restore['elapsed']:.2f}s"
        )

    all_passed = all(r["passed"] for r in results.values())
    print()
    if all_passed:
        print("🎉 全部测试通过!")
    else:
        print("❌ 存在失败的测试用例")
        sys.exit(1)


if __name__ == "__main__":
    main()
