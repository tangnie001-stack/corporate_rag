"""RRF 融合：纯函数，无需数据库，两路等权。"""

from src.infra.db.vector_store.types import ChunkResult


def _r(
    chunk_id: str,
    *,
    dense_rank: int | None = None,
    sparse_rank: int | None = None,
) -> ChunkResult:
    """构造测试用 ChunkResult，可显式指定分路排名（默认两路都未填）。"""
    return ChunkResult(
        id=chunk_id,
        content=f"内容 {chunk_id}",
        metadata={},
        dense_rank=dense_rank,
        sparse_rank=sparse_rank,
    )


def test_fusion_empty_inputs():
    """两路都为空时融合结果为空列表。"""
    from src.rag.fusion import rrf_fusion

    assert rrf_fusion([], [], k=60, top_n=50) == []


def test_fusion_dense_only_keeps_dense_rank():
    """只有 dense 路结果时全部保留，dense_rank 按位置兜底、sparse_rank 为 None。"""
    from src.rag.fusion import rrf_fusion

    result = rrf_fusion([_r("a"), _r("b")], [], k=60, top_n=10)
    assert [r.id for r in result] == ["a", "b"]
    assert [r.dense_rank for r in result] == [0, 1]
    assert [r.sparse_rank for r in result] == [None, None]


def test_fusion_sparse_only_keeps_sparse_rank():
    """只有词法路结果时全部保留，sparse_rank 按位置兜底、dense_rank 为 None。"""
    from src.rag.fusion import rrf_fusion

    result = rrf_fusion([], [_r("a"), _r("b")], k=60, top_n=10)
    assert [r.sparse_rank for r in result] == [0, 1]
    assert [r.dense_rank for r in result] == [None, None]


def test_fusion_both_paths_preserve_both_ranks():
    """同一结果出现在两路时，两侧排名都要保留（来源可辨）。"""
    from src.rag.fusion import rrf_fusion

    result = rrf_fusion([_r("a"), _r("b")], [_r("b"), _r("a")], k=60, top_n=10)
    by_id = {r.id: r for r in result}
    assert by_id["a"].dense_rank == 0
    assert by_id["b"].sparse_rank == 0


def test_fusion_two_paths_are_equal_weight():
    """两路等权：同一名次在两路贡献相同得分，融合不引入权重参数。

    两路各取第 1 名时得分相同 ⇒ `sorted` 稳定排序保持插入序（dense 路先遍历），
    "x" 排在 "y" 之前；若给 sparse 路更大权重，顺序翻转为 ["y"] —— 该断言对
    权重比例敏感，改（或新增）权重参数会使其失败。
    """
    from src.rag.fusion import rrf_fusion

    tie = rrf_fusion([_r("x")], [_r("y")], k=60, top_n=1)
    assert [r.id for r in tie] == ["x"]


def test_fusion_respects_top_n():
    """结果条数不超过 top_n。"""
    from src.rag.fusion import rrf_fusion

    result = rrf_fusion([_r(f"d{i}") for i in range(10)], [], k=60, top_n=3)
    assert len(result) == 3


def test_fusion_multi_three_way():
    """三路融合：跨路累加使两路命中的 "a" 排第一，顺序由得分确定且去重。"""
    from src.rag.fusion import rrf_fusion_multi

    merged = rrf_fusion_multi(
        [[_r("a"), _r("b")], [_r("a"), _r("c")], [_r("b"), _r("a")]], k=60, top_n=5
    )
    assert [r.id for r in merged] == ["a", "b", "c"]


def test_fusion_multi_respects_top_n():
    """多路融合条数不超过 top_n，且保留的是得分最高的前两名。"""
    from src.rag.fusion import rrf_fusion_multi

    merged = rrf_fusion_multi(
        [[_r("a"), _r("b")], [_r("a"), _r("c")], [_r("b"), _r("a")]],
        k=60,
        top_n=2,
    )
    assert len(merged) == 2
    assert [r.id for r in merged] == ["a", "b"]


def test_fusion_multi_fills_dense_rank_only():
    """多路融合只按位置兜底写 dense_rank，sparse_rank 保持未填（None）。"""
    from src.rag.fusion import rrf_fusion_multi

    merged = rrf_fusion_multi([[_r("a"), _r("b")], [_r("a")]], k=60, top_n=5)
    assert [r.dense_rank for r in merged] == [0, 1]
    assert [r.sparse_rank for r in merged] == [None, None]


def test_fusion_has_no_database_dependency():
    """fusion.py 自身不 import 数据库执行设施（AST 检查其导入语句）。

    允许的唯一 DB 相关导入是 `src.infra.db.vector_store.types`（纯类型定义）；
    其余 `src.infra.db.*` 目标以及 `sqlalchemy` / `asyncpg` / `asyncio` 一律禁止。
    用 AST 而非源码字面匹配，是为了拦住 `from src.infra.db...` 形式的真依赖
    —— 字面匹配只挡固定字符串，换个模块名（如 `vector_store.pg_store`）就漏。

    该检查只覆盖 fusion.py **自身**的导入语句，**不能**证明导入链上没有 DB 模块：
    `vector_store.types` 的导入会执行 `vector_store/__init__.py`，可间接拉起
    `pg_store`（内含 asyncio / session_factory）。delta 的真实要求是「融合能仅凭
    两路结果列表验证、不需要数据库连接」，由本文件其余用例以纯函数调用直接证明：
    它们不建立任何连接即可运行。
    """
    import ast
    import inspect

    import src.rag.fusion as fusion_module

    tree = ast.parse(inspect.getsource(fusion_module))
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            targets.append(node.module)

    allowed = {"src.infra.db.vector_store.types"}
    forbidden_prefixes = ("src.infra.db", "sqlalchemy", "asyncpg", "asyncio")
    for target in targets:
        if target in allowed:
            continue
        for prefix in forbidden_prefixes:
            assert not target.startswith(prefix), (
                f"fusion.py 不得依赖数据库执行设施：{target}"
            )
