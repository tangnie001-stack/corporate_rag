# query-rewrite Specification

## Purpose
TBD - created by archiving change query-rewrite-and-graph-simplification. Update Purpose after archive.
## Requirements
### Requirement: 独立 LLM 查询改写

系统 SHALL 对 medium / complex 路由的查询调用独立 LLM（flash 小模型，关闭思考模式）执行单任务改写，与 classify 分类调用分离。medium 路由 SHALL 输出单条 `standalone_query`（结合对话历史补全缺失约束的完整查询）；complex 路由 SHALL 输出 `sub_queries` 列表（2-4 条可独立检索的子查询，覆盖对比/多步分析的每个侧面）。

#### Scenario: medium 路由输出单条改写
- **WHEN** classify 判定路由为 medium，且查询含对话历史可推断的缺失约束（如历史"腾讯2024年营收多少"、当前"毛利率呢"）
- **THEN** rewrite 节点调用 LLM 输出单条 `standalone_query`（如"腾讯2024年毛利率是多少"），写入 `rewritten_queries`

#### Scenario: complex 路由输出子查询列表
- **WHEN** classify 判定路由为 complex（如"对比一下腾讯和东软的利润"）
- **THEN** rewrite 节点调用 LLM 输出 2-4 条子查询列表，每条均可独立检索，写入 `rewritten_queries`

#### Scenario: 无需改写的查询原样返回
- **WHEN** medium 路由且无对话历史、查询完整明确（如"腾讯2024年毛利率是多少"）
- **THEN** 不调用 LLM，`rewritten_queries` 保持为原始查询

### Requirement: 改写触发条件

系统 SHALL 仅在需要改写时才触发 LLM 调用：complex 路由必触发；medium 路由仅当"对话历史非空 或 查询长度 < 10 字符 或 含口语/省略词（如'呢'） 或 含分析/解释/说明/为什么等口语化前缀"时触发，否则跳过 LLM 直接返回原始查询。simple 路由不经过 rewrite 节点。含"分析/解释/说明/为什么"的查询触发后，LLM 改写应产出精简、可检索的查询（替代现状 `condense_query` 职责）。

#### Scenario: complex 必触发
- **WHEN** 路由为 complex
- **THEN** 必然调用 LLM 进行子查询分解

#### Scenario: medium 完整查询跳过改写
- **WHEN** 路由为 medium，无对话历史且查询完整明确
- **THEN** 不产生额外 LLM 调用，改写结果等于原始查询

#### Scenario: 口语化前缀查询触发精简
- **WHEN** 路由为 medium 且查询含"分析/解释/说明/为什么"（如"分析一下腾讯2024年营收"）
- **THEN** 触发 LLM 改写，输出精简可检索的查询（如"腾讯2024年营收"）

### Requirement: 改写约束保护

系统 SHALL 在改写 prompt 中约束 LLM：不得修改用户查询中已有的数字、公司名、报告期、否定词；仅在对话历史明确提供了约束（年份/公司/期间）时才将其补入改写查询；改写必须保持原语言（中文）并输出成句、可直接用于检索的完整查询。查询本身已完整时允许原样返回。

#### Scenario: 保留用户已有约束
- **WHEN** 用户查询为"腾讯2024年毛利率是多少"
- **THEN** 改写结果不得更改"腾讯""2024年""毛利率"等已有实体

#### Scenario: 历史约束补全不篡改
- **WHEN** 历史为"2023年净利润多少"、当前查询为"2024年呢"
- **THEN** 改写结果为"2024年净利润多少"，指标词"净利润"保持不变，仅替换期间

### Requirement: 改写失败回退

系统 SHALL 在 LLM 调用失败、输出非法 JSON 或输出为空时回退到规则改写（`expand_query`/`condense_query`/`decompose_query`）；规则改写仍无效时回退到原始查询。回退不应阻塞检索流程。

#### Scenario: LLM 调用异常回退
- **WHEN** LLM 改写调用抛异常或返回不可解析内容
- **THEN** 使用规则改写结果；规则无有效输出时使用原始查询继续检索
