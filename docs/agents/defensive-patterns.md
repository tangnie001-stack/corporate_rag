# 防御性模式

> 真实发生/差点发生的缺陷类别，写成防复发规则。写并发、进程级注册表、SSE 流式、精排、实体、prompt、接口契约、数据库、部署、开发期闸门相关代码前先读。

## 并发

### 共享资源并发访问必须串行化

**现象**：并行检索时多个线程/协程同时操作同一共享资源，而该资源本身不可并发安全，导致崩溃或数据错乱。

**规则**：对不可并发安全的共享资源，用锁（`threading.Lock` / `asyncio.Lock`）串行化访问；新增共享客户端时先判断是否线程安全。向量存储现为 PostgreSQL 连接池（并发安全），其 IO 方法为 `async`，并发检索直接 `asyncio.gather` 调用即可，回归用例在 `tests/infra/db/test_vector_store.py`。

### 每请求上下文的活跃状态必须按调用分槽

**现象**：一轮内多个 `delegate_task` 被 `asyncio.gather` 并发调度，若把 `delegate_id` / 停止原因写在共享 `RequestContext` 的单值字段上，多次委派互相覆盖，导致 SSE 增量、任务看板与终态判定串号。

**规则**：每次调用一个独立实例（`DelegateRun`，`src/agents/skills/delegate_run.py`）并由调用方逐层传递；若必须落到请求上下文，落到**该次调用的独立子 ctx**（`RequestContext.child()`），不挂共享单值字段。

## 进程级注册表

### 进程级注册表不得在构造期快照

**现象**：`SkillLoader` 在构造时读取并缓存 `readonly_map()`，而生产环境构造 loader 早于工具注册，快照到的只读表为空 → 双轴调用控制的 fail-safe 永不触发（Plan 1 最终评审发现）。

**规则**：需要进程级事实（工具只读表、注册表等）时，在**解析/使用期**惰性读取当前状态（`src/agents/skills/loader.py` 每次解析调用 `readonly_map()`），不在构造期缓存；构造器只保存可注入的覆盖值。

## SSE 流式

### 流事件必须按节点元数据匹配，不用顺序假设

**现象**：流式输出 token 丢失，因为 SSE 事件解析匹配不到正确的生成节点。

**规则**：SSE 事件解析通过 `metadata.langgraph_node` 精确匹配节点，不依赖事件顺序或索引。新增节点/事件类型时，同步检查流解析逻辑。

### 流式过程行的 DOM 挂载点必须先于气泡锚点固定

**现象**：思考块/状态行渲染到 AI 回答气泡的下方（上下错位），或多轮对话后各轮思考块跨轮堆叠无法区分归属（2026-09-08 chat.html 排版缺陷）。

**规则**：流式事件驱动的 DOM 追加必须挂到**本轮过程容器**（创建于任何气泡之前，惰性创建 + 每轮复位指针），禁止直接挂容器根级；禁止在流中途提前创建气泡等"未来锚点"（锚点创建后，容器级追加会全部落到锚点之后）。同轮的过程行生命周期保持一致——要么都随容器持久保留，要么都清理，不做单类型清理。

## 精排（rerank）

### 复杂合并结果必须 cap 到 TOP_K_RERANK

**现象**：复杂查询的多次检索结果合并后超出 `TOP_K_RERANK`，输出被截断或逻辑异常。

**规则**：任何 rerank 合并逻辑最终必须裁剪到 `TOP_K_RERANK` 上限。新增合并策略时检查上限约束。

## 词法检索（lexical）

### 分词口径漂移（写入与查询两侧不一致）

**形态**：写入侧（`chunks.content_seg`）与查询侧（tsquery 词元）用了不同的分词器、
不同的词典，或同一分词器的不同版本。**不报错，只静默降召回**；最隐蔽的一种是
**版本漂移** —— 分词结果随 `tsv` 生成列固化落库，升级 jieba 后存量与新的查询侧不一致，
而"同一进程内两个函数比较"的守卫测试抓不到它。

**防复发**：
1. 两侧只调 `tokenizer.tokenize()` 一个函数（守卫测试断言两侧词元逐字相等）；
2. `jieba` pin 精确版本；
3. 变更分词器配置/版本后**必须**跑 `scripts/rewrite_content_seg.py --apply`，
   并用 `--check`（退出码非 0 即存量已过期）作为验收动作；
4. 用户原文不得直接进 `to_tsquery` —— 含空格会抛错、`a:` 会被静默吞字符；
   查询串一律经 `lexical_query.build_lexical_query` 构造；
5. 词元全被滤掉时兜底走**正文子串**（不是"回退为不过滤"——写入侧没有单字 lexeme）。

## 实体提取

### 入库前过滤 null / 非标量实体值

**现象**：null 或 list/dict 等非标量实体值写入分块 metadata（`chunks.metadata` jsonb 列）时，写入或读取失败。

**规则**：实体值写入分块 metadata（`chunks.metadata` jsonb 列）前，过滤 null 与非标量值；抽取侧也要容错（抽取失败时优雅降级，不阻塞入库）。

### 标题/实体解析必须有 fallback 链

**现象**：文件名中的拉丁名无法匹配中文公司名，导致标题归属错误。

**规则**：标题/实体解析建立 fallback 链（如 heading → 文件名），单一路径失败时回退，不静默产出空值。

## Prompt

### 日期注入必须用北京时区

**现象**：提示词注入的日期用了服务器默认时区，与业务语义不符。

**规则**：prompt 中的日期/时间注入统一使用 `Asia/Shanghai` 时区。

### 无条件引用条件注册的工具

**症状**：prompt 里出现"调用 `search_web` 联网搜索"这类句子，但本轮该工具**并未注册**
（`settings.WEB_SEARCH_ENABLED=false`，或 `delegate_task` 因 skill 库为空而为 `None`）。
模型照做 → 报错或被拒 → 白耗一轮。

**根因**：文案的"挂载点"是**无条件**的，而工具是**条件注册**的。

**规则**：凡引用某个工具的规则，其判据 SHALL 是"该工具已注册"（适用域另计）。
判据住代码、不由 YAML 声明；逐条对照表见 `docs/agents/prompt-ownership.md` §3。

**历史实例**：
1. `KB_UNBOUND_SYSTEM_PROMPT` 无条件提及 `search_web`（后者受 `settings.WEB_SEARCH_ENABLED`
   条件注册，`rag_tools.py:240`）。
2. `tools-delegate-guidance` 无条件提及 `delegate_task`（skill 库为空时为 `None`）。

### 渲染器切换残留格式转义

**现象**：模板换了渲染器，旧渲染器时代的 `{{` / `}}` 转义没跟着去掉。`str.format`
会把双花括号还原成单花括号，而 `loader.render` **不做还原** —— JSON 示例于是原样输出
`{{...}}`，下游 `json.loads` 失败又被 `except` 吞掉、静默回退。

**规则**：切换渲染器 SHALL 在同一提交内去掉该渲染器特有的转义；消费点的 `except`
兜底 SHALL NOT 让解析失败无声通过。`loader.render` 的调用方 SHALL 配一条"渲染产物
无残留转义"的守卫测试。

**历史实例**：classifier / rewrite / entity 三个远端模板各有一条（提交 `0aab2e9`、`005c572`）。

## 接口契约

### 前端消费的响应形状必须以契约文档为准，且形状漂移只能降级不能抛错

**现象**：知识库管理页文档行的「详情」按钮点了没反应 —— `showEvalModal` 按**扁平**键读（`data.table_score`、`data.granularity_cv.toFixed(2)`），而后端 `eval_detail` 是**嵌套**结构（`structure_integrity.table.score`、`sbr.broken_boundaries[]`，且 `granularity_cv` 是对象不是数字）。`data.granularity_cv` 命中的是模块对象，`.toFixed` 抛 `TypeError`；该行位于 `sections` 构建期，**早于** `body.innerHTML` 与 `classList.add('show')` ⇒ 弹窗永不打开，且除控制台外无任何迹象（列表行上的 `分块评分 0.90 ✓` 反而正常，因为 `overall_score` 恰好在顶层）。缺陷存活 71 天（2026-07-12 引入 → 2026-09-22 发现）；同源的二级弹窗 `showEvalBroken` 三个分支全错，一级弹窗的断裂链接还传错键（传 `sec.key='structure'`，而查询表的键是 `'table'`）。

**规则**：前端读任何接口返回值前，形状以 `docs/agents/api_contract.md` 对应小节为准，**不靠猜字段名**（尤其别把后端局部变量名当 JSON 键）；改动消费同一字段的前端代码后，按 CLAUDE.md「契约同步」同时过 `tests/` 与 `deploy/nginx/html/` 两条消费链。另外对"取到的值再调数值方法"处加类型守卫（`typeof v === 'number' ? v.toFixed(2) : '—'`）—— **形状漂移只该降级成占位符，不该让整个组件不渲染**。

**历史实例**：`deploy/nginx/html/index.html` 的 `showEvalModal` / `showEvalBroken`（提交 `e503375` 引入；修复后同一契约形状见 `api_contract.md` §2.2.1）。

## 数据库

### UUID 列必须用 String(36)

**现象**：UUID（36 字符）存入 `String(32)` 列被截断，产生脏数据。

**规则**：主键/UUID 外键列用 `String(36)`（`src/infra/db/base.py`）。新增模型时遵循此约定。

### 派生写操作跨事务

**现象**：一次业务动作要写多张表（写分块 + 更新文档状态、删分块 + 软删文档/知识库），若各表各自提交，进程死在中间就产生**孤儿**：分块已删而状态已变，或分块未写而文档已 ready。更隐蔽的一种是删除路径**吞掉异常** —— 分块删除失败只 `warning` 后继续软删文档/知识库，孤儿永久存在且不可观测（历史缺陷：KB 删除的 `except Exception: logger.warning`）。

**规则**：跨表写用 `session_scope` / `Repo.transaction()` 打开**唯一**事务，参与者方法传 `session=`（传入时不提交，契约见 api_contract.md §4），失败即整体回滚，**且不得吞异常** —— 删除失败必须向上抛，由调用方决定呈现。实现点：`_write_chunks_and_mark_ready`、`DocumentService.delete_document`、`AppService.delete_knowledge_base`。

## 存储迁移

### 派生副本与权威来源分离时，读取侧必须显式重建契约键

**现象**：字段从 blob/jsonb 升为独立列，或从「每库一集合」改为「单表 + 归属列」后，读取侧若直接把存储行原样返回，依赖契约键的下游会**静默失效** —— 不报错，只是取空或丢键。P2 把 `doc_id` / `chunk_index` / `chunk_total` / `source` / `page` 从 metadata 升为 `chunks` 列即属此类：`_dedup_by_parent` 读 `doc_id`/`parent_content`、引用渲染读 `source`/`page`、实体透传读自定义键，列里没有副本就断链（`rag/retrieval.py`、`agents/tools/rag_tools.py`）。

**规则**：跨存储 / 跨表示的字段搬迁必须有一个**唯一**的映射函数：写入侧用它把契约键从源 metadata 剔除（`split_metadata()`），读取侧用它把权威列值 + jsonb 平铺合并回对外契约对象（`src/infra/db/vector_store/mapping.py::row_to_chunk_result()`，冲突以列为准）。该函数必须有单测钉住全部契约键与 jsonb 自定义键的原样透传；禁止各读取路径各自拼装 metadata。

### 存储替换的验收必须与算法变更分离

**现象**：把「换存储」和「改算法/参数」混在一次变更里，验收时无法区分行为变化来自哪一侧 —— 「新实现自己跑得通」不能证明替换前后结果一致。

**规则**：存储替换**不应改变行为**，判据必须是**差分**：同一份语料 + 同一批固定查询，比对替换前后 top-k 重合率（P2 用 ≥0.9）；替换期间不得顺带改融合权重、top-k 口径或分词，两侧共用同一 embedder 以隔离存储变量；查询集与脚本落盘可复现。⚠ 差分闸门要同时说明**灵敏度上限** —— P2 的 k=30 / 阈值 0.9 取均值口径，允许单条约 3 个位次偏差、最多容忍 2 条查询完全错位，且单位化向量下 cosine / L2 / 内积排序等价 → 不能据此宣称「迁移零风险」。

## 依赖

### 删依赖前先查它是不是别人的传递提供者

**现象**：P4 退役 Chroma 时删除 `chromadb` 依赖，而 `bcrypt` 此前**仅由 `chromadb` 传递提供**、并未显式声明，但 `src/utils/auth_crypto.py` 直接 `import bcrypt` —— 依赖一删，应用启动即 `ModuleNotFoundError` crash-loop。整个仓库只有 `pyproject.toml:43-45` 注释记下了这条链路，文档与依赖列表本身看不出风险。

**规则**：删除一个依赖前，先确认本仓库代码或其它依赖没有在隐式消费它传递提供的包 —— 全仓检索对它的 `import`、并核对被删依赖的依赖树；被直接 `import` 的传递依赖必须提升为**显式运行时依赖**并加注释说明来源（`pyproject.toml:43-45` 的 `bcrypt` 即此形态，标注不可移除），删除后重建镜像并跑一次启动自检。

## 部署

### 进程退出必须优雅停机

**现象**：服务重启时正在处理的请求被强行中断，连接未释放。

**规则**：uvicorn 配置 graceful shutdown 超时，退出前等待在途请求完成。

### 流式生成状态必须留在进程内（单 worker 部署）

**现象**：prod 曾配 `--workers 4`。若流式生成依赖进程内状态（任务注册表、事件缓冲），多 worker 下续接/取消请求可能落到别的进程，导致状态读不到、任务找不到。

**规则**：生产环境以单 worker 部署（uvicorn 不配 `--workers`）。流式生成的任务注册表、事件缓冲等状态留在进程内，不引入 Redis 共享/多 worker 支持。新增流式或部署相关设计时遵循此约束；确需横向扩展时先评估，不得默认多 worker。

### 单 worker 下的阻塞调用

**现象**：生产单 worker（见上一条），任何同步阻塞调用都会冻住整个进程 —— 所有请求与 SSE 一起卡住。两类已识别的阻塞面：① 同步外网调用（DashScope embedding / rerank、解析）；② CPU 密集的 jieba 分词（首次加载词典约 0.5–1 s）。二者在 `PgVectorStore.add_chunks` 等处已用 `asyncio.to_thread` offload 到线程池。

**规则**：阻塞调用不得放在事件循环线程上 —— 同步外网 / CPU 工作一律走 `asyncio.to_thread`。**事务内不得包含外网调用**：embedding 等慢调用必须在**进入事务之前**算好，事务只接收已算好的向量（`design.md` D7）—— 否则事务长占连接与锁，单 worker 下把连接池拖垮。

### compose bind mount 不写绝对路径

**现象**：`docker-compose.override.yml` 曾把 6 条开发期挂载写成主工作区绝对路径（`/mnt/.../corporate_rag/{src,tests,skills,agents}` 与 `deploy/nginx/{nginx.conf,html}`）。危害两层，都是静默的：① 仓库 clone / 挪到别的路径后 bind 源不存在（Docker 可能报错，也可能静默建成**空目录**），容器看不到 `src/` 与 nginx 内容；② 在 `git worktree` 里跑 compose 时挂的仍是**主工作区**的文件 —— worktree 的改动静默失效，且看起来像生效了。

**规则**：compose 的 bind mount 一律写**相对路径**（`./src:/app/src`）。相对 bind 按「compose 文件所在目录」解析，**与调用时的 cwd 无关**，因此主工作区与各 worktree 各自解析到自己。主 compose（`./data/ragas`、`./deploy/postgres/init`）原本就是此写法，override 曾偏离。

## 开发期闸门（pre-commit）

### 闸门的失败面要按"输入类"界定，不能只看当前仓库可不可达

**现象**：`src/cli/check_docs.py` 的代码快照只 `except OSError`，**不捕获 `UnicodeDecodeError`**（它是 `ValueError` 子类）。而该钩子 `doc anti-rot (all docs)` 是 `always_run` 且无 `files` 过滤 —— **每次提交都全量跑**。因此只要 `src/` 下出现**一个**非 UTF-8 的 `.py`，闸门就会抛异常 ⇒ **全员的每一次提交都被拦死**。本仓当前无非 UTF-8 文件，故不可达 —— 但"不可达"靠的是输入集恰好干净，不是代码保证。

**规则**：给"每次提交都跑"的闸门写文件读取时，异常覆盖面要按**输入类**界定，而不是按"当前仓库里有没有这种文件"。① 遍历全树时，单文件失败必须**跳过该文件**，不能崩掉整轮（`OSError` 之外还要想到 `UnicodeDecodeError` 这类解码失败）；② **不得把"捕获范围保留、但触发面被放大"的改动说成"行为不变"** —— 触发面本身就是行为的一部分（"命中即短路"可能永远读不到坏文件，"必读全树"则每次都会撞上）。

**历史实例**：2026-09-23 change `check-docs-symbol-lookup-perf`。把"逐符号重读（命中即短路）"换成"单次快照（必读全树）"时，捕获范围逐字保留，但**触发面被扩大**（存在坏文件时每次查询都抛）。当时先在 `design.md` D6 写成"保留现状"，被代码评审指出不准确后才改准。待修项登记在 `requirements_pool.md` D-08。

## 如何更新

修复非平凡 bug 且属于"可复发缺陷类别"时，按"现象 → 规则"格式追加条目到对应分区。
判断标准：这个坑换个地方还会踩吗？会 → 记；不会 → 不记。
分块相关的坑进 chunking-issues.md，不进本文。
