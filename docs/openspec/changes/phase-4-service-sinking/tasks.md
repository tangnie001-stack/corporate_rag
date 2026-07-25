## 1. 密码工具模块（auth-crypto）

- [ ] 1.1 新建 `src/utils/auth_crypto.py`，实现 `hash_password()` 和 `verify_password()` 函数
- [ ] 1.2 编写 `tests/utils/test_auth_crypto.py` 测试密码哈希与校验逻辑

## 2. AuthService 下沉

- [ ] 2.1 新建 `src/services/auth_service.py`，实现 `AuthService` 类（register / login / verify_token）
- [ ] 2.2 重写 `api/auth.py`：只保留路由和参数校验，业务逻辑调 AuthService
- [ ] 2.3 在 `AppService` 上暴露 `auth_service` 属性，注入 `MySQLDB` 给 AuthService
- [ ] 2.4 编写 `tests/services/test_auth_service.py`，mock AuthService 依赖

## 3. Document upload 下沉

- [ ] 3.1 在 `src/services/document_service.py` 上新增 `store_and_process()` 方法
- [ ] 3.2 重构 `api/documents.py` 的 upload handler，只调 `svc.document.store_and_process()`
- [ ] 3.3 更新 `tests/api/test_documents.py` 测试

## 4. API 层直调修复

- [ ] 4.1 `api/sessions.py`：`svc.db.*` → 通过 AppService 委托方法
- [ ] 4.2 `api/chat.py`：`svc.chat_manager.*` → 通过 AppService 委托方法
- [ ] 4.3 `api/kb_eval.py`：`svc.db.get_latest_eval_report()` → 通过 AppService 委托方法
- [ ] 4.4 `api/ragas_generate.py`：`svc.db.get_kb_by_name()` → 通过 AppService 委托方法
- [ ] 4.5 `api/health.py`：`from src.config import MAX_FILE_SIZE` → 通过 AppService 属性
- [ ] 4.6 `api/llm_test.py`：`from src.config import DASHSCOPE_API_KEY/BASE_URL` → 通过 AppService 属性

## 5. 死代码清理

- [ ] 5.1 删除 `src/rag/chain.py`
- [ ] 5.2 更新 `src/rag/__init__.py`，移除 `RAGChain` 的 re-export
- [ ] 5.3 `api/chat.py` 的 import 从 `src.api.sse_utils` 改为 `src.utils.sse`，删除 `api/sse_utils.py`
- [ ] 5.4 删除失效测试文件：`tests/rag/test_chain.py`、`test_stream.py`、`test_prompt.py`、`test_rag_chain_tracing.py`
- [ ] 5.5 更新 `tests/services/test_app_service.py`（移除 14 处 RAGChain mock）
- [ ] 5.6 更新 `tests/eval/test_eval_ragas.py`（移除 chat_with_citations mock）

## 6. 验证

- [ ] 6.1 `pytest tests/ -v` 全部通过
- [ ] 6.2 `ruff check .` 无错误
- [ ] 6.3 无遗留 `print()`、TODO 或调试代码
- [ ] 6.4 各层 import 合规检查（api/ 不导入 infra/、config/）
