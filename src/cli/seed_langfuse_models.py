"""把模型定价写入 Langfuse（幂等）。

**为什么是幂等 CLI 而不是 UI 手配**：dev / prod 要各自落地同一份定价，手配不可复现；
而 Langfuse v2 的 `LANGFUSE_INIT_*` 只能播种 project / user / key，**不能播种模型定价**。

**为什么 0 要跳过**：0 是「未配置」的哨兵（见 `settings.py`），输入/输出两侧都必须
配置齐全。任一侧为 0 都跳过：写入一个缺价（如 output 为 0）的模型定义会把补全 token
按零价计，成本被静默低估，且下游 `calculated_total_cost > 0` 的成本闸门仍会通过；
写一个单侧为 0 的定义比不写更糟。

**为什么 pattern 必须锚定**：服务端用 Postgres 的 `~` 做匹配，那是**子串**正则 ——
未锚定的 `(?i)qwen3.8-flash` 会命中 `prefix-qwen3.8-flash` / `qwen3.8-flash-old`
这类形近名，把单价套到别的模型上（是**误配**，不是匹配不到）。

用法：
    python -m src.cli.seed_langfuse_models            # 按 settings 写入
    python -m src.cli.seed_langfuse_models --dry-run  # 只打印将写入的内容

退出码：0 = 正常（含 skipped / exists）；1 = 失败（配置缺失或后端不可达）。
"""

import argparse
import asyncio
import re
import sys
from typing import Any

from langfuse.api import CreateModelRequest
from langfuse.decorators import langfuse_context

from src.config import (
    LANGFUSE_ENABLE,
    LLM_MODEL,
    MODEL_INPUT_PRICE_PER_TOKEN,
    MODEL_OUTPUT_PRICE_PER_TOKEN,
)

#: Langfuse 模型定义的计价单位（配合 input_price/output_price 即 USD / 单 token）
UNIT_TOKENS = "TOKENS"

#: `models.list` 的翻页大小
LIST_PAGE_SIZE = 100


def build_model_request(
    model_name: str, input_price: float, output_price: float
) -> dict[str, Any]:
    """构造 Langfuse 模型定义的请求参数。

    Args:
        model_name: 模型名（取自 `settings.LLM_MODEL`）
        input_price: 输入单价（USD / 单 token）
        output_price: 输出单价（USD / 单 token）

    Returns:
        含 `model_name` / `match_pattern` / `unit` / `input_price` / `output_price`
        的字典；`match_pattern` 为锚定正则，由 `re.escape` 构造以防误配。
    """
    return {
        "model_name": model_name,
        "match_pattern": "(?i)^" + re.escape(model_name) + "$",
        "unit": UNIT_TOKENS,
        "input_price": input_price,
        "output_price": output_price,
    }


def _list_model_names(models: Any) -> set[str]:
    """翻页列出 Langfuse 侧已有的模型名。

    Args:
        models: `client_instance.api.models` 客户端

    Returns:
        已存在的模型名集合
    """
    names: set[str] = set()
    page = 1
    while True:
        response = models.list(page=page, limit=LIST_PAGE_SIZE)
        rows = response.data or []
        for row in rows:
            names.add(row.model_name)
        if len(rows) < LIST_PAGE_SIZE:
            return names
        page += 1


async def seed(
    client: Any, model_name: str, input_price: float, output_price: float
) -> str:
    """幂等写入一条模型定价。

    Args:
        client: Langfuse 客户端（生产传 `langfuse_context.client_instance`）
        model_name: 模型名
        input_price: 输入单价（USD / 单 token）
        output_price: 输出单价（USD / 单 token）

    Returns:
        "skipped"（单价未配置完整）/ "exists"（同名已存在）/ "created"（本次创建）

    单价需两侧齐全：任一侧为 0（未配置哨兵）即跳过。
    """
    if input_price == 0.0 or output_price == 0.0:
        print(
            f"[skip] 单价未配置完整（in={input_price} out={output_price}）"
            "——不写入会低估成本的模型定义"
        )
        return "skipped"

    models = client.api.models
    if model_name in _list_model_names(models):
        print(f"[exists] 模型定义已存在，跳过：{model_name}")
        return "exists"

    request = build_model_request(model_name, input_price, output_price)
    models.create(request=CreateModelRequest(**request))
    print(
        f"[created] {model_name} "
        f"pattern={request['match_pattern']} "
        f"unit={request['unit']} "
        f"in={input_price} out={output_price} (USD/token)"
    )
    return "created"


def run(argv: list[str] | None = None) -> int:
    """CLI 入口。

    Args:
        argv: 命令行参数（None 时取 sys.argv[1:]）

    Returns:
        退出码：0 正常；1 失败
    """
    parser = argparse.ArgumentParser(description="把模型定价写入 Langfuse（幂等）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写入")
    args = parser.parse_args(argv)

    request = build_model_request(
        LLM_MODEL, MODEL_INPUT_PRICE_PER_TOKEN, MODEL_OUTPUT_PRICE_PER_TOKEN
    )
    if args.dry_run:
        print(f"[dry-run] 将写入：{request}")
        return 0

    if not LANGFUSE_ENABLE:
        print("[error] LANGFUSE_ENABLE=false，跳过")
        return 1

    client = langfuse_context.client_instance
    try:
        asyncio.run(
            seed(
                client,
                LLM_MODEL,
                MODEL_INPUT_PRICE_PER_TOKEN,
                MODEL_OUTPUT_PRICE_PER_TOKEN,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 后端不可达等统一收敛为一行
        print(f"[error] 写入失败：{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
