# UI 设计流程

改 UI 或新增组件时，走 `ui-ux-pro-max` skill：

1. **生成全局设计系统**：
   ```bash
   python3 ~/.agents/skills/ui-ux-pro-max/scripts/search.py "<产品类型> <关键词>" --design-system -p "项目名"
   ```
   持久化到 `docs/design/MASTER.md`（加 `--persist` 参数）

2. **页面级设计**：写入 `docs/design/pages/<page-name>.md`（中文），包含视觉规格和交互说明

3. **效果预览**：只出独立 HTML 文件（`docs/design/<名字>-mockup.html`），不截图
   用户直接用 Chrome 打开本地文件查看（无需挂 nginx/localhost）

4. 所有设计文档用中文，提交到 git
5. **知识防腐**：前端（chat.html 等 UI）一旦变动，必须同步更新 `docs/design/MASTER.md` 与对应 `pages/*` 规格，防止设计与实现脱节
6. **文件名日期后缀**：新建设计文件（`.md` 规格与 `.html` 预览）文件名以生成日期 `-YYYY-MM-DD` 结尾（扩展名前，如 `chat-delegate-progress-2026-09-07.md`），便于识别文件生成时间；历史文件不追溯改名
