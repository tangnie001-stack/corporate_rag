## 1. 依赖注入基础设施

- [ ] 1.1 新建 `src/api/dependencies.py`：实现 `get_app_service` 延迟初始化单例

## 2. API 模块改造

- [ ] 2.1 改造 `src/api/auth.py`：删除 `_service`/`_get_service()`，路由函数改用 `Depends(get_app_service)`
- [ ] 2.2 改造 `src/api/knowledge_base.py`：同上
- [ ] 2.3 改造 `src/api/documents.py`：同上
- [ ] 2.4 改造 `src/api/sessions.py`：同上
- [ ] 2.5 改造 `src/api/chat.py`：同上
- [ ] 2.6 改造 `src/api/kb_eval.py`：同上

## 3. 测试改造

- [ ] 3.1 `conftest.py` 新增 `mock_app_service` fixture（使用 dependency_overrides）
- [ ] 3.2 改造 `test_knowledge_base.py`：`@patch` → `mock_app_service`
- [ ] 3.3 改造 `test_documents.py`：同上
- [ ] 3.4 改造 `test_chat.py`：同上
- [ ] 3.5 改造 `test_auth.py`：同上
- [ ] 3.6 改造 `test_sessions.py`：同上
- [ ] 3.7 改造 `test_kb_eval.py`：同上

## 4. 验证

- [ ] 4.1 运行 `python -m pytest tests/api/ -v` 确认全部通过
- [ ] 4.2 运行 `ruff check .` 确认无错误
