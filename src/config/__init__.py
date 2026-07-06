"""配置包 — 集中管理环境参数、SQL 查询语句和 LLM 提示词。

使用方式：
  from src.config import MEMORY_WINDOW              # settings 中的常量
  from src.config.queries import INSERT_DOCUMENT    # SQL 语句（推荐显式路径）
  from src.config.prompts import FINANCIAL_SYSTEM_PROMPT  # 提示词模板（推荐显式路径）
"""

from src.config.settings import *  # noqa: F403 — 重新导出所有配置常量
