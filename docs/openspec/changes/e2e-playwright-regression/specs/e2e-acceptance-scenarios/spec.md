> **SUPERSEDED（2026-09-07）**：本规范中"态 A 未跑 judge"相关验收场景已随在线忠实度 judge
> 移除作废（态 A/B 均不再跑 judge）；其余场景不变。

## ADDED Requirements

### Requirement: 受控 KB 造数 fixture

验收场景测试 SHALL 能通过后端 document upload API 在用例内自建/自清受控知识库（fixture helper，如 `ensureKb(name, docs[])` 建库并返回 kb_id，用例结束删除），用于构造触发 verify/护栏/联网的行为前提；不得依赖环境里既有的共享业务 KB。

#### Scenario: 用例自建受控 KB

- **WHEN** 一条需要绑 KB 的验收用例执行
- **THEN** 该用例经上传接口创建独立 KB（含用例指定的文档内容），用完即清理，不依赖外部预置 KB

#### Scenario: 触发条件的可控性

- **WHEN** 用例需要触发缺失年份/漏标引用等行为
- **THEN** fixture 提供的文档内容 SHALL 能定向构造该触发条件（如仅含部分年份、正文含可引用数据）

### Requirement: 后端日志断言钩子

测试 SHALL 提供只读的日志断言 helper：给定用例捕获的 `X-Trace-ID`，在 app 容器内对该 trace 的日志行执行 grep（`docker compose exec app grep <trace> /data/logs/app_*.log`），支持断言事件行存在/缺失（如 judge 未执行、search_web 已执行、`retrieval_signal:` 行出现）。该 helper SHALL 不修改任何前端或后端源码。

#### Scenario: 断言后端事实

- **WHEN** 用例需验证"态 A 未跑 judge / regen 轮 search_web 已执行 / 触发检索行为信号"
- **THEN** 用捕获的 trace_id 在容器日志 grep 对应事件行断言存在或缺失

#### Scenario: 依赖锚点分级

- **WHEN** 日志断言锚定 `[verify]` 层事件
- **THEN** 该断言 SHALL 标注"依赖 logging-convention-migration 3.2"并在其落地后启用；`[retrieval]`/`retrieval_signal:` 锚点在 logging 3.1（已交付）基础上即刻可用

### Requirement: 验收场景分层

E2E 验收场景 SHALL 分两层：**确定性回归层**（结构断言、可秒级重跑、作为稳定门禁：chat 主链路、工具状态 detail、SSE resume 回放、引用横条）与**行为冒烟层**（受控 KB 尽力触发 verify 护栏补标 / 态 A 不跑 judge / regen 后 search_web 完整执行 / 缺年份联网仍缺标注 / 检索行为信号行；断言结构与 trace→日志；依赖 LLM 随机行为的用例失败 SHALL 自动附 trace_id 与截图并允许转人工，不作为硬门禁）。

#### Scenario: 分层运行与门禁语义

- **WHEN** 执行 E2E 回归
- **THEN** 确定性层全部通过视为门禁通过；冒烟层以报告为准，失败项附 trace_id 转人工复核

### Requirement: 跨 change 验收闭环标注

E2E 场景跑通后 SHALL 能在其所承接的旧 change（agent-loop-hardening / retrieval-quality-signals / sse-tool-detail / logging-convention-migration 及 P0 agent-harness 遗留）的 tasks.md 对应人工验证项上交叉标注"由 e2e-playwright-regression 覆盖闭环"，避免同一验收跨 change 重复执行。

#### Scenario: 旧 change 待验项闭环

- **WHEN** e2e 的某验收场景稳定通过
- **THEN** 对应旧 change 的"待人工验证"项被标注为已由本 change 覆盖，随其归档或保持已闭环注记
