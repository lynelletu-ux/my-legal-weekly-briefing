# 每周歌曲推荐榜｜Durable Publish Layer

本目录用于长期保存《🎵每周歌曲推荐榜》的正式 HTML 周榜，解决 ChatGPT 计划任务临时附件可能出现“此文件已过期 / 找不到此文件”的问题。

## 固定目标

- Repository：`lynelletu-ux/my-legal-weekly-briefing`
- Branch：`main`
- Pages Source：`main /docs`
- 音乐归档首页：`docs/music/index.html`
- 正式 HTML：`docs/music/issues/YYYY/issue-NN.html`
- Pages 根路径：`https://lynelletu-ux.github.io/my-legal-weekly-briefing/music/`
- 单期 URL：`https://lynelletu-ux.github.io/my-legal-weekly-briefing/music/issues/YYYY/issue-NN.html`
- Commit message：`publish: weekly music issue NN`

底层 URL 路径只使用小写英文字母、数字、连字符和斜杠。中文标题与 emoji 只用于页面标题和聊天入口文字。

## 正式交付规则

1. 完成候选筛选、历史去重、Apple Music CN 精准核验。
2. 生成最终单文件 HTML，保持现有无图深色 Editorial Music Journal 版式。
3. 做 HTML QC：UTF-8、移动端可读、无 `<img>`、每首歌 Apple Music 按钮为具体 song URL。
4. 根据正式历史确定期号，不得因为重试而跳号或重置。
5. 将 HTML 写入 `docs/music/issues/YYYY/issue-NN.html`。
6. 重新读取 GitHub 文件，确认内容、日期、期号与标题一致。
7. 检查对应 GitHub Pages HTTPS URL。短暂 404 视为部署 pending，应复核同一 URL，不得重复创建新期号。
8. 页面可访问后更新 `docs/music/index.html`，将本期放在最前。
9. 最终聊天层只推送一个稳定 Pages 入口；不再使用 sandbox 临时附件作为正式交付。

## 聊天显示

链接文字必须严格为：

`🎵每周歌曲推荐榜｜YYYY.MM.DD｜第NN期.html`

链接目标必须为该期 GitHub Pages 稳定 URL，而不是 `sandbox:/mnt/data/...`。

## HTML 内容规则

- HTML 内容、版式、歌曲推荐逻辑、Apple Music 精准直达按钮均保持现行规则。
- 正式取消所有歌曲/专辑/歌手图片。
- Apple Music 链接仅存在于 HTML 内。
- 聊天层不显示 Apple Music 卡片、播放器、搜索结果卡、歌曲 URL 或歌单正文。

## 失败恢复

- GitHub 写入失败：不发送伪造 URL，不消耗新期号。
- 文件已写入但 Pages 尚未部署：保持 pending，复核同一路径。
- 不因失败重试而产生第二个同期期号或重复文件。
