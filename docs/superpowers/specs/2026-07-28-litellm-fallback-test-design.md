# LiteLLM Fallback 自动化测试方案

## 背景

LiteLLM Proxy 已配置模型 fallback：qwen3.7-max（DashScope）失败时自动切换至 deepseek-v4-flash（DeepSeek）。需要自动化脚本来验证该机制正常工作，并测量切换性能。

## 测试脚本

文件：`tests/test_litellm_fallback.py`

### 测试流程

```
1. 备份 config.yaml → config.yaml.bak
2. 写入配置测试文件（qwen3.7-max key 改为错误值）
3. restart proxy
4. 运行测试用例
5. 恢复 config.yaml.bak → config.yaml
6. restart proxy
7. 验证恢复后正常
```

脚本使用 try/finally 确保恢复始终执行。

### 测试用例

#### Case 1：正常路径基线

```
目的：    建立正常响应的耗时基线
操作：    curl POST /chat/completions，model=qwen3.7-max
断言：    HTTP 200，响应内容正常
记录：    端到端耗时 T_normal
```

#### Case 2：Fallback 切换

```
目的：    验证故障时自动切换到 deepseek-v4-flash
前置：    config.yaml 中 qwen3.7-max 的 api_key 改为错误值，重启 proxy
操作：    curl POST /chat/completions，model=qwen3.7-max
预测：    qwen3.7-max 认证失败 -> 重试 3 次 -> fallback 到 deepseek-v4-flash
断言：    HTTP 200
         响应头 x-litellm-model-id = "deepseek-v4-flash"
记录：    端到端耗时 T_fallback
         切换延迟 = T_fallback - T_normal
```

#### Case 3：恢复后正常

```
目的：    验证恢复原始配置后 qwen3.7-max 正常工作
前置：    恢复 config.yaml，重启 proxy
操作：    curl POST /chat/completions，model=qwen3.7-max
断言：    HTTP 200，响应内容正常
```

### 衡量指标

| 指标 | 说明 | 预期 |
|------|------|------|
| T_normal | 正常请求耗时 | < 10s |
| T_fallback | 含重试 + fallback 的总耗时 | < 40s |
| 切换延迟 | T_fallback - T_normal | - |

### 文件结构

```
litellm/
├── config.yaml          # 生产配置（脚本不修改此文件）
├── config.test.yaml     # 测试配置，与 config.yaml 唯一区别是 qwen3.7-max key 错误
└── config.yaml.bak      # 脚本运行时临时备份

tests/
└── test_litellm_fallback.py  # 测试脚本
```

### 错误处理

- 启动失败重试 1 次
- 请求超时设 60s
- 任何异常 → 恢复配置 + 重启 proxy
- 退出时自动清理临时文件

## 不涉及的范围

- SDK 直测方案（需额外安装 litellm 包）
- Embedding 模型的测试（不支持 fallback）
- 内容策略 / 上下文窗口 fallback
