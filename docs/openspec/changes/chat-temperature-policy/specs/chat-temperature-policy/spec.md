# chat-temperature-policy Specification (Delta)

## ADDED Requirements

### Requirement: 主 agent 温度按 KB 绑定分档

主 agent 的采样温度 SHALL 按请求是否绑定 KB（`kb_id` 非空）分档：绑定 KB（RAG 链路）为 0.1；未绑定 KB 为 0.6（默认）。同一请求内各 agent 轮次使用一致的档位。

#### Scenario: 绑定 KB 走低温
- **WHEN** 会话绑定 kb_id
- **THEN** 该请求主 agent 全轮温度=0.1

#### Scenario: 未绑定 KB 走中温
- **WHEN** 会话未绑定 kb_id
- **THEN** 该请求主 agent 全轮温度=0.6

#### Scenario: 同请求温度一致
- **WHEN** 一次生成包含多轮 agent 调用
- **THEN** 各轮使用同一档位温度，不随轮次变化

### Requirement: 温度档位可配置

温度档位默认值 SHALL 集中配置（settings）；配置变更不改需求语义。

#### Scenario: 默认档位可调
- **WHEN** 修改非 KB 默认温度配置
- **THEN** 非 KB 请求使用新默认值，KB=0.1 档不变

### Requirement: 分档仅作用于主 agent 采样

fork 子代理与分类/检索等内部调用 SHALL 不受本分档影响；子代理采样参数语义由 skill 层另行定义（本 change 不引入 skill temperature 字段）。

#### Scenario: 子代理不受主 agent 分档影响
- **WHEN** 主 agent 按 kb_id 分档生成
- **THEN** fork 子代理采样参数仍按既有 skill/model 解析规则
