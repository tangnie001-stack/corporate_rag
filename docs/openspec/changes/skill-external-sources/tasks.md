## 1. 前置核实（决定 git 来源是否落地）

- [ ] 1.1 实测容器内是否有 `git` 二进制（`docker exec corporate-rag-app git --version`）→ 决定 D1 的第三类来源（git 仓库）是否本期落地；无 git 则本期只做"直连单文件 + 归档"，并把该结论写回 design
- [ ] 1.2 确认 `skills/` 挂载在两个 compose 文件里都是目录级映射（新增 `.staging` 子目录不需要额外挂载）
- [ ] 1.3 确认加载器扫描逻辑对 `.` 开头目录的处理现状（`src/agents/skills/loader.py`）—— 若未忽略，本变更需先补上

## 2. 来源适配器与归约层

- [ ] 2.1 定义中间形态（一组 `(name, skill_md_content, extra_files)`）与来源适配器接口（`fetch(source) -> list[Candidate]`）
- [ ] 2.2 直连单文件适配器（HTTP/HTTPS，`httpx`）
- [ ] 2.3 归档适配器（zip / tar，支持 URL 与本地路径；`zipfile` / `tarfile`）
- [ ] 2.4 git 目录适配器（浅克隆 + 子目录 + ref）—— 依赖 1.1 结论
- [ ] 2.5 单测：三种来源各自的正常路径；网络失败；归档损坏

## 3. 校验（复用既有契约，不新写）

- [ ] 3.1 落盘前用 `SkillLoader` 的解析函数对候选内容做"预加载"，解析失败即拒绝
- [ ] 3.2 `name` ASCII slug 校验（复用既有正则）
- [ ] 3.3 与既有 skill 名冲突 → fail-fast（复用 `SkillRegistry` 语义），不静默覆盖
- [ ] 3.4 单测：frontmatter 缺失 / name 非 slug / 名冲突 / 同上但用目录名缺省

## 4. 安全边界（外部输入不可信，本期必须覆盖）

- [ ] 4.1 **路径穿越**：归档条目名含 `../` 或绝对路径 → 拒绝整个归档
- [ ] 4.2 **解包炸弹**：解包后总字节数与条目数超上限 → 拒绝（上限放 `src/config/`）
- [ ] 4.3 单测：`../` 条目、绝对路径条目、超限归档、条目数超限
- [ ] 4.4 单测：符号链接条目（归档内 symlink 指向 `/etc` 之类）—— 若解包实现会跟随，则必须拒绝

## 5. 原子落盘与清理

- [ ] 5.1 暂存目录 `skills/.staging/<uuid>/`（与目标同文件系统）
- [ ] 5.2 全部校验通过后逐个 `os.replace` 到 `skills/<name>/`
- [ ] 5.3 任一失败 → 清理暂存目录，`skills/` 不留部分结果
- [ ] 5.4 加载器忽略 `.` 开头目录（与 1.3 联动）
- [ ] 5.5 启动或 CLI 入口清理超过 N 小时的 `.staging/*`（崩溃残留）
- [ ] 5.6 单测：校验中途失败后无残留；`.staging` 不被识别为 skill

## 6. CLI 与来源记账

- [ ] 6.1 CLI 入口（如 `python -m src.cli.install_skill <source> [--subdir] [--ref]`），输出安装结果与拒绝原因
- [ ] 6.2 含可执行内容的来源 → 明确拒绝并输出"本期只获取、不安装"（不得静默丢弃脚本后成功）
- [ ] 6.3 来源记账文件（`skills/<name>/.source.json`：来源类型 / 来源标识 / 安装时间）；**不写入 SKILL.md frontmatter**
- [ ] 6.4 安装后触发注册表重载（或说明需重启）
- [ ] 6.5 单测：记账文件内容；SKILL.md frontmatter 未被污染

## 7. 文档与收尾

- [ ] 7.1 `docs/agents/defensive-patterns.md` 登记"外部内容解包"风险类别（路径穿越 / 解包炸弹 / 覆盖既有 skill），并补一条：**外部 skill 正文会进模型上下文 → 安装是 prompt 注入面，必须是显式人工动作**
- [ ] 7.2 `docs/agents/cookbook.md` 记录"从外部来源安装 skill"的可复用操作流程
- [ ] 7.3 `CLAUDE.md` 的 `skills/` 说明补一句"外部来源安装后仍落在同一读取面"
- [ ] 7.4 `docs/agents/glossary.md` 登记术语：skill 获取（acquisition）/ 读取面 / 暂存目录
- [ ] 7.5 跑 `pytest tests/ -v` + `ruff check .` + `pyright src/` + `python -m src.cli.check_docs`
- [ ] 7.6 `openspec validate skill-external-sources` 通过后归档
