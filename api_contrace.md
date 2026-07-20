# API 接口契约

## POST /api/ragas/generate

触发 RAGAS 测试集生成（同步，等待生成完成后返回）。

### Request
```json
{
  "kb_name": "test123",
  "size": 20
}
```

### Response (成功)
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "version": 2,
    "testset_size": 20
  }
}
```

### Response (失败)
```json
{
  "code": 1,
  "message": "知识库 'xxx' 不存在",
  "data": null
}
```

### 说明
- `kb_name`: 知识库名称
- `size`: 生成的 QA 对数，默认 20（来自 `settings.RAGAS_TEST_SIZE`）
- `version`: 生成测试集的版本号
- `testset_size`: 实际生成的 QA 对数
- 同步接口，生成完成后才返回响应
- 依赖 MinIO、MySQL 等 Docker 内部服务，需在容器内运行
