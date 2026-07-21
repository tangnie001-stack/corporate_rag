# UI 设计流程

改 UI 或新增组件时，走 `ui-ux-pro-max` skill：

1. **生成全局设计系统**：
   ```bash
   python3 ~/.agents/skills/ui-ux-pro-max/scripts/search.py "<产品类型> <关键词>" --design-system -p "项目名"
   ```
   持久化到 `docs/design/MASTER.md`（加 `--persist` 参数）

2. **页面级设计**：写入 `docs/design/pages/<page-name>.md`（中文），包含视觉规格和交互说明

3. **效果预览**：只出独立 HTML 文件（`docs/design/<名字>-mockup.html`），不截图
   通过 `http://localhost/<mockup文件名>.html` 查看

4. 所有设计文档用中文，提交到 git
