from src.infra.db.file_store import FileStore

def test_build_path():
    p = FileStore.build_path("u1", "kb1", "d1", "rpt.pdf")
    assert p == "documents/u1/kb1/d1/rpt.pdf"
