"""存量文档清除重建脚本 — 清 ChromaDB chunks + 重置 status + 重触发入库。

策略：保留 document 记录（含 MinIO file_path），只清 ChromaDB chunks 并重置
status=pending，重新触发 process_document 跑新链路（pymupdf4llm + 实体抽取），
使存量文档的 chunk metadata 带上实体字段（company / report_period / sec_code）
并聚合到 document.meta_info["entities"]。

用法:
  python -m scripts.rebuild_kb_data --kb-id <kb_id>   # 重建单个 KB
  python -m scripts.rebuild_kb_data --all             # 重建所有有文档的 KB

注意:
  - 破坏性操作：会清空指定 KB 的 ChromaDB chunks，执行前确认目标。
  - 必须在 app 容器内执行（宿主机无法解析 minio/chroma 容器名）：
      docker compose exec app python -m scripts.rebuild_kb_data --kb-id <kb_id>
"""
import argparse
import asyncio
import os
from collections import Counter

from src.infra.db.engine import session_factory
from src.infra.db.mysql_db import DocumentRepo, KbRepo
from src.infra.db.vector_store import VectorStore
from src.parsers.router import DocRouter
from src.services.document_service import DocumentService


def _clear_chromadb(vector_store: VectorStore, kb_id: str) -> int:
    """清空指定 KB 的 ChromaDB chunks。

    Args:
        vector_store: 向量存储实例
        kb_id: 知识库 ID

    Returns:
        删除的 chunk 数量
    """
    collection = vector_store.get_or_create_collection(kb_id)
    ids = collection.get(include=[])["ids"]
    if not ids:
        return 0
    collection.delete(ids=ids)
    return len(ids)


async def _rebuild_kb(
    vector_store: VectorStore, svc: DocumentService, kb_id: str
) -> None:
    """重建单个 KB：清 ChromaDB + 重置 status + 重触发 process_document。

    Args:
        vector_store: 向量存储实例
        svc: 文档处理服务
        kb_id: 知识库 ID
    """
    repo = DocumentRepo(session_factory)
    docs = await repo.get_documents(kb_id)
    if not docs:
        print(f"KB {kb_id}: no documents, skip")
        return

    # 1. 清 ChromaDB chunks（先清空，确保重跑入库时无旧 chunk 残留）
    try:
        deleted = await asyncio.to_thread(_clear_chromadb, vector_store, kb_id)
        print(f"ChromaDB: deleted {deleted} chunks in kb_{kb_id}")
    except Exception as e:  # noqa: BLE001
        print(f"ChromaDB: clear failed for kb_{kb_id}: {e}")

    # 2. 重置 status=pending（保留 document 记录与 MinIO 文件，不丢数据），
    #    顺带清空上次处理的错误/进度/分块数，避免旧状态残留误导
    for d in docs:
        await repo.update_document_status(
            d.id,
            "pending",
            processing_state=None,
            processing_progress=0,
            processing_message=None,
            error_msg=None,
            chunk_count=0,
        )
    print(f"MySQL: reset {len(docs)} documents to pending for kb {kb_id}")

    # 3. 重新触发入库：直接调 process_document，minio_key = d.file_path
    #    跳过无 file_path 的文档（仅测试直接建库的记录，MinIO 无对应文件，
    #    无法重跑入库；已重置为 pending，保持原状态）。其余 gather 等待全部
    #    完成，避免脚本退出时后台任务被取消导致文档卡在 processing
    ingestible = [d for d in docs if (d.file_path or "").strip()]
    skipped = len(docs) - len(ingestible)
    if skipped:
        print(f"KB {kb_id}: skip {skipped} documents without MinIO file")
    if not ingestible:
        return
    tasks = [
        svc.process_document(
            kb_id,
            d.id,
            d.file_path,
            d.filename,
            os.path.splitext(d.filename)[1].lower() or ".pdf",
        )
        for d in ingestible
    ]
    await asyncio.gather(*tasks, return_exceptions=True)
    # process_document 内部捕获异常并标 failed，按重跑后最终 status 汇总打印
    ingestible_ids = {d.id for d in ingestible}
    fresh_docs = await repo.get_documents(kb_id)
    status_counts = Counter(d.status for d in fresh_docs if d.id in ingestible_ids)
    print(
        f"KB {kb_id}: re-ingest finished for {len(ingestible)} documents, "
        f"status={dict(status_counts)}"
    )


async def main() -> None:
    """命令行入口：按 --kb-id 或 --all 解析目标知识库并逐库重建。"""
    parser = argparse.ArgumentParser(description="存量文档清除重建脚本")
    parser.add_argument("--kb-id", help="要重建的知识库 ID")
    parser.add_argument("--all", action="store_true", help="重建所有有文档的知识库")
    args = parser.parse_args()

    if args.all:
        kbs = await KbRepo(session_factory).get_all_kb()
        kb_ids = [kb.id for kb in kbs]
    elif args.kb_id:
        kb_ids = [args.kb_id]
    else:
        raise SystemExit("需指定 --kb-id 或 --all")

    vector_store = VectorStore()
    doc_repo = DocumentRepo(session_factory)
    svc = DocumentService(doc_repo, vector_store, DocRouter())

    for kb_id in kb_ids:
        print(f"=== rebuild KB {kb_id} ===")
        await _rebuild_kb(vector_store, svc, kb_id)


if __name__ == "__main__":
    asyncio.run(main())
