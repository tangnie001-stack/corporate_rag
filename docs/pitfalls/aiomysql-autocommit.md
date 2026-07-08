# aiomysql 连接池 autocommit 事务残留

## 现象

DELETE / UPDATE 操作后，立刻查询文档列表，**交替**返回旧数据和新数据（如 `2, 1, 2, 1, 2, 1`）。重启容器后暂时正常，过一段时间又出现。

## 根因

### aiomysql 的默认行为

`aiomysql.create_pool()` 的 `autocommit` 参数**默认值为 `False`**，这会覆盖 MySQL 服务端默认的 `autocommit=True`。

当 `autocommit=False` 时：
1. 每条 `SELECT` 语句会隐式开启一个事务（REPEATABLE READ 隔离级别）
2. 在代码中虽然有 `await conn.commit()` 来关闭事务，但 aiomysql 连接池内部的事务状态追踪存在 bug
3. 连接归还到池时，**事务没有被正确清理**
4. 后续请求拿到同一个连接时，读到的是该连接之前未提交的旧数据快照

### 官方确认

这是 aiomysql 的已知问题：

- [Issue #999: Default autocommit should be None](https://github.com/aio-libs/aiomysql/issues/999)（2025年4月）
- 根本原因：`autocommit=False` 在连接初始化时执行 `SET autocommit=0`，覆盖了服务端配置
- 连接释放时，事务残留导致后续查询读到过期数据

## 修复

```python
# aiomysql 连接池初始化时开启 autocommit
self._pool = await aiomysql.create_pool(
    ...
    autocommit=True,  # 显式开启
)
```

## autocommit=True 的影响

| 场景 | autocommit=False（原） | autocommit=True（新） |
|------|------------------------|------------------------|
| 查询一致性 | ❌ 事务状态跨请求残留 | ✅ 每条 SQL 独立提交 |
| 批量 INSERT | 多条在一个事务，性能好 | 每条单事务，稍慢 |
| 多语句事务 | 隐式支持但边界模糊 | 需显式 `conn.begin()`/`commit()` |
| SELECT 开销 | 隐式开启事务，有开销 | 无事务开销 |

## 适用场景

- **单条 SQL 操作**（大多数 API 请求）：`autocommit=True` 更安全
- **需要多条 SQL 原子性操作**（如转账）：用 `conn.begin()` + `conn.commit()` 显式控制事务

## 相关代码

- `src/infra/db/mysql_db.py` — `_get_pool()` 方法中的连接池配置
- 相关数据流：`api/documents.py → app_service.py → mysql_db.py`
