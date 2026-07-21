# 代码依赖图使用说明

`.codegraph/codegraph.db` — SQLite，含全量代码节点和调用/引入关系。

## 表结构

- **files**: 所有项目文件（path, language, size, node_count）
- **nodes**: 所有代码实体（kind: class/function/method/variable/import/route），含精确行号、签名、docstring
- **edges**: 代码关系（kind: contains/calls/imports/instantiates/references/extends）
- **unresolved_refs**: 未解析的引用

## 常用查询

```sql
-- 查看所有 API 路由
sqlite3 .codegraph/codegraph.db "SELECT name, file_path, start_line FROM nodes WHERE kind='route';"

-- 查看所有文件和节点数
sqlite3 .codegraph/codegraph.db "SELECT path, language, node_count FROM files;"

-- 查看某函数被谁调用
sqlite3 .codegraph/codegraph.db "SELECT source FROM edges WHERE kind='calls' AND target IN (SELECT id FROM nodes WHERE name='函数名');"

-- 查看所有 import 关系
sqlite3 .codegraph/codegraph.db "SELECT source, target FROM edges WHERE kind='imports' LIMIT 20;"
```

理解代码关系时优先用此查询，比逐文件 grep 高效。
