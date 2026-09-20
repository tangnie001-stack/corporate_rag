"""提示词模板集 — 集中管理所有 LLM prompt。

本包由两部分组成：
  - `templates/` 下的 YAML 模板：全链路提示词正文（系统指令、用户消息模板、
    分类/改写/实体抽取任务模板、引用编号指令等），经 `loader.get_content(template_id)`
    读取；占位符统一使用 {var} 格式。
  - 本模块内的 `VERIFY_*` / `FORK_*` 常量：agent 运行期注入的 verify 指引与
    delegate fork 执行契约。它们不走模板化 —— 与 const.py 的 *_MARKER 查重短语
    配对，是供业务代码遍历 messages 查重的行为键，而非可自由改写的正文。

设计原则：
  1. 提示词正文集中在 templates/ 中，而非散落在业务模块里
  2. 系统指令与用户模板分离，便于分别调试和优化
  3. 需要跨模块查重的指令文案：完整文案在此，查重标记短语在 const.py
     （业务代码按标记短语查重防重复注入，改文案不破坏查重逻辑）

与 queries.py 的关系：
  queries.py 管理发给 MySQL 的 SQL 语句
  prompts（templates/ 与本模块）管理发给 LLM 的提示词
  两者互不依赖，但遵循相同的"常量集中管理"理念
"""

from src.config.const import FORK_CONFIRM_MARKER

# ====== agent 运行期 verify 指引 ======

# 以下指令由 verify 护栏在 agent 循环运行期注入为 SystemMessage，用于驱动 agent
# 重生成。每条指令内含一段查重标记短语（const.py 的 *_MARKER）——业务代码遍历
# messages 查重防重复注入时按该短语匹配。完整文案集中于此、标记短语在 const.py
# （二者必须保持"短语存在于文案中"，改文案时标记短语随文同步，查重不破）。

# verify 完整联网指引 — 知识库缺失年份且用户已确认联网时注入，驱动 agent 调用
# search_web 补充缺失年份数据后重新回答。内含 {missing}（缺失年份列表）与
# {marker}（const.VERIFY_GUIDANCE_MARKER，查重短语）。渲染消息示例：
#   "知识库缺失年份 [2023, 2025]，用户已确认联网，请调用 search_web 工具补充这些年份的数据后再回答。"
VERIFY_GUIDANCE_PROMPT: str = (
    "知识库缺失年份 {missing}，{marker}，"
    "请调用 search_web 工具补充这些年份的数据后再回答。"
)

# verify "一次带全" hint — 完整指引已注入但 agent 上一轮 search_web queries 仍带漏
# 缺失年份时，独立补发一条带全提示（重申轮也须送达）。内含 {missing}（缺失年份
# 列表）与 {marker}（const.VERIFY_HINT_MARKER，查重短语，值为"一次带全以下年份"，
# 直接前缀 {missing}，无空格）。渲染消息示例：
#   "缺失年份 [2023, 2025] 仍未补全：search_web 支持一次传入多个查询，请再调用一次
#    search_web，一次带全以下年份[2023, 2025] 对应的查询后重新回答。"
VERIFY_HINT_PROMPT: str = (
    "缺失年份 {missing} 仍未补全：search_web 支持一次传入多个查询，"
    "请再调用一次 search_web，"
    "{marker}{missing} 对应的查询后重新回答。"
)

# verify 联网引用标注指引（态 A，无 KB 场景）— 本轮调过 search_web 但回答未带 [n]
# 来源编号时注入，驱动 agent 补标注后重生成一次。内含 {marker}
# （const.VERIFY_CITATION_MARKER，查重短语）。
VERIFY_CITATION_GUIDANCE_PROMPT: str = (
    "你刚才的回答引用了联网搜索结果，但没有标注来源编号，"
    "{marker}，请在引用来源的对应句末补上 [n] 编号"
    "（编号须与搜索结果返回的来源列表一致）后重新回答。"
)

# verify KB 强制溯源指引（态 B）— 检索到 KB context 但答案无 [n] 时注入，驱动 agent
# 补标后重生成一次。内含 {marker}（const.VERIFY_KB_CITATION_MARKER，查重短语）。
VERIFY_KB_CITATION_GUIDANCE_PROMPT: str = (
    "你刚才的回答基于知识库检索结果，但没有标注来源编号，"
    "{marker}，请在引用来源的对应句末补上 [n] 编号"
    "（编号须与检索返回的来源列表一致）后重新回答。"
)


# ====== delegate fork 执行者（子代理装配） ======

# fork skill 正文未声明任务占位符（const.SKILL_TASK_PLACEHOLDERS）时的追加段：
# 把主 agent 委托的任务文本拼到正文末尾，保证子代理始终拿到任务。占位符 {task}
# 为 str.format 字段，渲染后即为任务原文（不再二次替换）。
FORK_TASK_APPEND_TMPL: str = "任务：{task}"

# fork 执行者预设缺省时的系统人设（create_agent 的 system_prompt）。skill 正文承载
# "按哪本手册干"（初始 user message），本常量承载"谁在干活"的默认身份。Plan 3 会与
# prompt 组装器对齐（build_system_prompt(persona=...) 的产物）。
FORK_DEFAULT_EXECUTOR_PROMPT: str = (
    "你是一个企业知识库智能助手，作为委派子代理执行被指派的具体任务。"
    "只依据任务给出的材料与方法论作答，不得编造；输出结构化分析文本，不标注引用编号 [n]。"
)

# fork 执行契约（design D18）：追加进子代理 system prompt，要求子代理需要用户确认时
# 单独输出一行 `CONFIRM_REQUIRED: <问题>` 并停止作答，编排层据此规则检测（0 LLM 调用）
# 复用澄清链路问用户。契约是执行约束，不随执行者人设（preset）内容作者意愿而增减。
FORK_EXECUTION_CONTRACT: str = (
    "\n\n执行契约（必须遵守）："
    f"如果你需要用户先确认才能给出结论，请单独输出一行 `{FORK_CONFIRM_MARKER} <你的问题>`"
    "并停止作答，不要自行假设后给出结论；其余情况直接给出结论。"
)
