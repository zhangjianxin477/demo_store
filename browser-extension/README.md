# CoreNote Web Clipper（Edge MVP）

当前版本用于验证完整采集链路：Edge 当前网页/选区 → CoreNote 文件资源库，并针对复杂文档页保留 Markdown 结构、表格和正文图片。

## 本地安装

1. 打开 Edge，访问 `edge://extensions/`。
2. 开启“开发人员模式”。
3. 点击“加载解压缩的扩展”，选择当前目录 `browser-extension`。
4. 将 CoreNote Web Clipper 固定到工具栏。
5. 打开插件设置，默认填写 `http://localhost:8080`。先在 CoreNote 页面登录，插件会自动读取登录态；也可手动填写 Access Token。
6. 本地测试通过后，再将地址改为 `https://corenote.cloud`。

## 当前接口

插件调用文件资源采集接口：

```text
POST /api/v1/file-resources/capture
```

请求字段：`url`、`title`、`content`、`selection`、`description`、`author_name`、`published_at`、`source_type`、`captured_at`、`folder_prefix`、`images`。

文件默认保存到 `自动抓取/YYYY-MM-DD/标题.md`，可在 CoreNote 文件资源树中查看。

正文图片会下载到同目录的 `assets/` 子目录，并在 Markdown 中生成预览链接；源站拒绝下载时会跳过该图片但不影响正文保存。

采集器会按页面顺序保留标题、段落、列表、引用、表格和图片。HTML 表格会转换为 Markdown 表格，并处理 `rowspan` / `colspan`；图片会保留在对应正文位置。若站点提供同源 `text/markdown` 页面（例如部分 Hugging Face 文档），优先使用该权威 Markdown，避免多栏导航、侧边栏和目录污染正文。没有结构化端点时，会从匹配页面标题的语义标题开始，并在原创声明、评论区、相关产品/课程、页脚备案等强边界处停止，减少腾讯云开发者社区等页面的正文后噪声。微信公众号页面会优先使用 `#js_content` 正文容器，并过滤账号信息、阅读器入口、二维码、评论区和底部推荐。

## 当前限制

- 当前版本已将插件提取的完整正文和结构化元数据直传给文件资源接口，服务器不再依赖二次抓取。
- 采集结果当前先落盘，后续可接入 Agent 清洗、分块、向量化和审核队列。
- 动态网页需等待页面加载完成后再点击插件；截图和整页 PDF 尚未接入。复杂多栏页面若没有 Markdown 端点，仍以 DOM 可见顺序线性化保存，不会伪造原始视觉布局。
- Edge 商店正式发布前需要补充 PNG 图标、隐私政策、商店截图、权限说明和隐私数据声明。

## 登录态同步

插件会查找已打开的 CoreNote 标签页，并读取 `kh_access_token`（localStorage/sessionStorage），自动附加 `Authorization: Bearer` 请求头。若未找到登录页，会提示先登录。

部署到云端时，只需将后端代码（`backend/app/api/routes.py`、`backend/app/main.py`）同步部署，并在插件设置中把 CoreNote 地址改为云端 HTTPS 地址；插件会按该地址查找登录页并写入云端文件资源库。
