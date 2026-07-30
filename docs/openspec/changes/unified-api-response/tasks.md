- [ ] 1. 新增 `src/api/schema.py` — ResponseModel 定义
       ```python
       class ResponseModel(BaseModel):
           code: str = "SUCCESS"
           msg: str = "操作成功"
           data: Any
       ```
- [ ] 2. 修改 `response_processor_middleware` —
       - 去掉 body_iterator 读取和 json.loads
       - 去掉 JSONResponse 包装，直接 `return response`
       - 保留非 GET 日志，但不记 body
       - 保留 except → log → re-raise
- [ ] 3. 改造 `knowledge_base.py` (3 handlers → 包 ResponseModel)
- [ ] 4. 改造 `documents.py` (5 handlers)
- [ ] 5. 改造 `sessions.py` (3 handlers)
- [ ] 6. 改造 `auth.py` (4 handlers，含 logout/anonymous 改为 ResponseModel)
- [ ] 7. 改造 `health.py` (2 handlers)
- [ ] 8. 改造 `kb_eval.py`、`ragas_generate.py`、`llm_test.py` — 替换现有 BaseResponse
- [ ] 9. 删除 `response.py` 中的 `BaseResponse` 类（确认其他地方已无引用）
- [ ] 10. 验证: `pytest tests/ -v` 全部通过
- [ ] 11. 验证: `ruff check .` 无错误
