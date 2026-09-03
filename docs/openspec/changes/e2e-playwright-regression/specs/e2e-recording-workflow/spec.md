## ADDED Requirements

### Requirement: 录制工作流

E2E 用例 SHALL 支持通过 playwright-cli 录制真实联调走查来创建：操作者（人工或 agent）用 playwright-cli 在 `http://localhost:8080` 上走查流程，每个动作生成等价 Playwright TS 代码，收集拼接为 spec.ts 草稿。录制产生的草稿 SHALL 未经 review 不得作为最终用例资产提交。

#### Scenario: 录制生成 spec 草稿

- **WHEN** 操作者用 playwright-cli 走查并执行填表/点击/提交等动作
- **THEN** 每个动作输出等价 Playwright TypeScript，可收集为一个 spec.ts 草稿文件

#### Scenario: 草稿需经 review

- **WHEN** 生成 spec.ts 草稿后
- **THEN** 由人工或 agent review 删改误操作与走错分支的步骤，保留正确操作后才提交入库

### Requirement: 用例断言规范

录制生成的用例 SHALL 以产品级可观察结果作为断言目标（页面元素存在性、状态标签文案、URL 变化、消息渲染结果），不得断言 LLM 输出的逐字内容，不得依赖易变的 DOM 结构细节。断言文案若来自后端 SSE 交互事件，SHALL 与 `src/config/const.py` 的 `SSEInteractionTexts` 对齐。

#### Scenario: 断言结构特征而非逐字答案

- **WHEN** 用例断言助手消息渲染结果
- **THEN** 断言消息气泡/富文本元素出现等结构特征，不断言与历史 LLM 输出逐字一致

#### Scenario: 状态标签断言对齐文案源

- **WHEN** 用例断言工具状态标签出现
- **THEN** 断言的状态文案与 `SSEInteractionTexts` 中定义的固定文案一致

### Requirement: 用例独立性与清理

每条 E2E 用例 SHALL 独立运行（自建会话/数据、自清理），不得依赖其它用例的执行结果或共享可变数据，避免测试间污染。

#### Scenario: 用例可独立执行

- **WHEN** 单独运行某条用例
- **THEN** 该用例不依赖前序用例留下的会话或状态，可独立通过

#### Scenario: 用例结束清理

- **WHEN** 用例创建了会话等数据
- **THEN** 用例在结束（成功或失败）时清理其创建的数据
