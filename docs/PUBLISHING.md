# Durable Publish Layer 发布规范

本目录用于长期保存已经完成编辑、质量检查并正式发布的单文件 HTML 期刊。它与仓库根目录的 Public Feed 数据层相互独立。

## 固定目标

- Repository：`lynelletu-ux/my-legal-weekly-briefing`
- Branch：`main`
- Pages Source：`main /docs`
- 正式 HTML：`docs/issues/YYYY/issue-NN.html`
- Archive：首页 `docs/index.html`
- Commit：`publish: legal journal issue NN`

底层路径仅使用小写英文字母、数字、连字符和 `/`。中文标题与 emoji 只用于页面内展示，不进入 URL。

## 正式期号

只有通过全部 HTML QC、写入 GitHub、确认文件存在且 Pages 可访问的正式成刊才消耗期号。`test`、`shadow`、`preview` 和 `dry-run` 不得写入 `docs/issues/`，也不得加入 Archive。

不得覆盖其他期号或删除历史期刊。修正同一期时只允许在明确确认期号相同后更新其原路径。

## 正式发布顺序

1. 完成最终 HTML，保持 UTF-8、单文件且不依赖外部 assets 才能阅读。
2. 执行 HTML QC 与隐私检查。
3. 根据最后一个已经成功持久发布的期号确定新期号。
4. 通过 GitHub Contents API 在 `main` 写入 `docs/issues/YYYY/issue-NN.html`。
5. 确认 GitHub 文件存在。
6. 等待 Pages 部署并检查对应 HTTPS URL；短暂 404 应标记为 `pending` 并稍后复核，不得重复创建文件。
7. 页面可访问后，更新 `docs/index.html`，将新一期入口置于历史列表最前。
8. 再次确认 Archive 链接、正式页面与期号一致，随后向用户返回稳定 Pages URL。

## HTML QC

正式文件必须非空，并至少包含：`<!DOCTYPE html>`、UTF-8 声明、正确正式标题、“本期目录｜CONTENTS”、正文和本期运行报告。路径中的年份、期号必须与正文一致。

公开内容不得包含任何登录凭据、访问密钥、浏览器会话状态、本机绝对路径、账户信息、私人案件事实、当事人信息或内部认证材料。

## Archive 更新规则

- 只为已经成功发布的正式 HTML 增加入口。
- 新一期按时间倒序排列。
- 链接使用相对路径，例如 `issues/2026/issue-01.html`。
- 展示名可使用 `📰我的法律实务期刊｜YYYY.MM.DD—MM.DD｜第NN期`。
- 历史入口不得删除或改指向其他期号。

## URL 规则

站点根路径为仓库配置返回的 GitHub Pages URL。正式期刊 URL 由该根路径加 `issues/YYYY/issue-NN.html` 组成。只有实际 HTTP 验证通过后才能声明发布成功。

## 失败恢复

如果 GitHub 写入失败，不更新 Archive、不消耗期号。如果文件已写入但 Pages 仍在部署，记录为 `pending` 并复核同一 URL；不要重复提交相同期号。若 QC 或隐私检查失败，停止发布并保留上一期稳定 Archive。
