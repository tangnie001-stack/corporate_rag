## Context

Phase 3 重构消除了同步 RAG 流水线和 `asyncio.new_event_loop()` 反模式后，架构评估发现了 6 类问题。其中优先级最高的是 Service 层下沉和 API 层违规直调——这是 Phase 3 有意未覆盖的"第二阶段"工作。

当前状态：
- `api/auth.py` 中约 11 行登录/注册业务逻辑直调 `svc.db.*`，未经过 Service 层
- `api/documents.py` 的 upload handler 混合了 MinIO 上传、MySQL 写入、后台任务创建三个关注点
- 7 个 api/ 文件共 18 处直调 `svc.db.*` / `svc.chat_manager.*` / `from src.config import ...`
- `rag/chain.py` 是 121 行的死代码类，零生产调用方
- `api/sse_utils.py` 是 3 行桥接文件，仅被 `api/chat.py` 引用

## Goals / Non-Goals

**Goals:**
- 将 auth 业务逻辑从 API 层下沉到 `AuthService`
- 将 document upload 的全流程封装到 `DocumentService.store_and_process()`
- 消除 api/ 层对 infra/db/config 的直接调用（7 个文件，18 处）——通过 AppService 委托
- 删除 `rag/chain.py` 和 `api/sse_utils.py` 死代码
- 修复所有失效的测试文件

**Non-Goals:**
- 不涉及超限文件拆分（mysql_db.py / vector_store.py 等，放最后做）
- 不涉及新的外部依赖
- 不改动 LangGraph 节点逻辑
- 不添加新功能，纯重构+清理

## Decisions

### D1 — AuthService 采用独立类 + 工具函数模式
- 新建 `src/services/auth_service.py`，内含 `AuthService` 类
- 新建 `src/utils/auth_crypto.py`，内含密码哈希/校验独立函数（bcrypt）
- `AuthService.__init__` 接收 `MySQLDB` 实例，不依赖 AppService
- 理由：password hashing 是纯函数逻辑，与业务编排无关，适合放在 utils/ 层；AuthService 专注编排（调 db + 调 crypto + 返回结果）

### D2 — DocumentService.store_and_process() 内含 create_task
- 在 `DocumentService` 上新增 `store_and_process(kb_id, file, user_id)` 方法
- 方法内依次调用 `self._upload()`（MinIO）→ `self._db.add_document()` → `asyncio.create_task(self.process_document(...))`
- API handler 只调这一个方法，返回 `{"doc_id": ..., "filename": ...}`
- 理由：API 层不应感知后台处理的存在；未来换成 Celery 只需改 Service 层

### D3 — API 直调统一通过 AppService 委托修复
- 在 `AppService` 上对每个被直调的方法加委托属性/方法
- 原则：api/ 代码中的 `svc.db.xxx()` → `svc.xxx()`，`svc.chat_manager.xxx()` → `svc.xxx()`
- config 读取：`from src.config import MAX_FILE_SIZE` → `svc.settings.MAX_FILE_SIZE`。AppService 上暴露 `settings` 属性返回 `src.config.settings` 单例，api/ 层通过 `svc.settings.X` 访问配置，不直接 import config

### D4 — RAGChain 直接删除，不强留兼容层
- `rag/chain.py` 零生产调用方，直接删文件
- `rag/__init__.py` 中移除 `from src.rag.chain import RAGChain`
- `RAGContext` 保留（仍在 retrieval.py / prompt.py / nodes.py 中使用）
- 4 个失效测试文件一并删除

### D5 — api/sse_utils.py 改为直接 import
- `api/chat.py` 的 import 从 `from src.api.sse_utils import ...` 改为 `from src.utils.sse import ...`
- 删除 `api/sse_utils.py`

## Risks / Trade-offs

- **[Risk] AuthService 新增文件，虽然小但影响登录注册流程** → Mitigation: 接口签名不变，返回格式不变，只做代码位置的移动
- **[Risk] AppService 委托方法过多** → Mitigation: 只加实际需要的委托，不加预判性封装。后续如果 AppService 膨胀到 400+ 行再考虑拆分
- **[Risk] store_and_process 内 create_task 异常不会被 API handler 捕获** → Mitigation: process_document 内部已有 try/except，不会抛未捕获异常
- **[Trade-off] 不拆超限文件** → 本次不动 mysql_db.py 等大文件，目的是减少冲突范围。拆分是纯机械操作，放最后做最安全
