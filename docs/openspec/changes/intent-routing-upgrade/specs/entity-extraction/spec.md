## ADDED Requirements

### Requirement: Regex entity extraction

系统 SHALL 通过正则表达式从用户查询中提取财务场景的关键实体，0 LLM 成本。

支持实体类型：
- year: 年份（2023、2024）
- quarter: 季度（一季度、Q1、第三季度）
- month: 月份（1月、12月）
- metric: 财务指标（营收、利润、毛利率、ROE 等）
- money: 金额（100亿、¥500万）
- percentage: 百分比（15%、3.5%）
- company: 公司名（华为、腾讯、腾讯公司）

#### Scenario: Year extracted
- **WHEN** 用户输入 "2024年营收多少"
- **THEN** EntityExtractor 提取 entity type=year, value="2024"

#### Scenario: Metric extracted
- **WHEN** 用户输入 "毛利率是多少"
- **THEN** EntityExtractor 提取 entity type=metric, value="毛利率"

#### Scenario: Money + year combined
- **WHEN** 用户输入 "2023年净利润100亿"
- **THEN** EntityExtractor 提取 [year=2023, metric=净利润, money=100亿]

#### Scenario: No entity in query
- **WHEN** 用户输入 "你好"
- **THEN** EntityExtractor 返回空列表

### Requirement: Entity result as LLM context

EntityExtractor 的输出 SHALL 作为 Tier 3 LLM 的输入上下文，帮助 LLM 判断缺失实体。

#### Scenario: Partial entities passed to LLM
- **WHEN** EntityExtractor 提取到 metric="营收" 但未提取到 year
- **THEN** LLM prompt 中包含已提取实体列表，LLM 可补充标记 missing_entities

### Requirement: LLM supplementary entity extraction

当正则无法提取到实体时，LLM SHALL 在 classify 调用中补充提取缺失实体信息。

#### Scenario: LLM detects missing year
- **WHEN** 用户输入 "营收多少" 且历史对话未提及年份
- **THEN** classify LLM 输出 missing_entities = [{"type": "year"}]

#### Scenario: LLM resolves entity from history
- **WHEN** 用户上一轮问过 "2023年营收多少"，本轮问 "利润率呢"
- **THEN** classify LLM 推断 year=2023，不标记为缺失
