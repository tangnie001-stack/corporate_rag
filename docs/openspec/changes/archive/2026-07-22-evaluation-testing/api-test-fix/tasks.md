## 1. 公共测试基础设施

- [ ] 1.1 创建 `tests/api/conftest.py`，包含 `client` 和 `auth_client` fixture
- [ ] 1.2 更新 `pyproject.toml` 添加 `api` marker

## 2. 修复现有测试

- [ ] 2.1 修复 `test_knowledge_base.py`：更新 4 处 patch 路径 + 补响应校验 + 改用 auth_client
- [ ] 2.2 修复 `test_documents.py`：更新 3 处 patch 路径 + 补 5 个新端点测试
- [ ] 2.3 修复 `test_chat.py`：mock 方法从 `chat_with_citations` 改为 `search`/`rerank`/`stream_answer`

## 3. 新增测试文件

- [ ] 3.1 新建 `tests/api/test_auth.py`：login / verify / logout / anonymous 测试
- [ ] 3.2 新建 `tests/api/test_sessions.py`：list / messages / delete 测试
- [ ] 3.3 新建 `tests/api/test_kb_eval.py`：eval/latest 测试

## 4. 验证

- [ ] 4.1 运行 `python -m pytest tests/api/ -v` 确认 20+ 测试全部通过
- [ ] 4.2 确认测试能真实拦截接口回归（验证 mock 路径和方法是否正确）
