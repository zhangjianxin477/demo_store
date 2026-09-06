# Security Policy

## 不要提交的内容

- `.env`、任何 `*.env`（除 `.env.example`）
- OpenAI/智谱/硅基流动/Zilliz/Milvus/SMTP/微信 Token
- `data/`、用户文档、向量索引、任务记录、备份和日志
- SSL 私钥、证书和云平台凭据

## 密钥管理

本项目只从环境变量或服务器本地配置读取密钥。生产部署请使用云 Secret Manager、GitHub Actions Secrets 或权限为 `600` 的服务器配置文件。不要把密钥写入前端 JavaScript，因为浏览器端代码对用户可见。

## 密钥泄露处理

如果密钥出现在 Git、截图、日志、聊天或构建产物中：

1. 立即在服务商控制台撤销旧密钥。
2. 生成新密钥并更新服务器 Secret。
3. 检查访问日志和账单。
4. 清理 Git 历史，不能只删除当前文件。

## 报告漏洞

请通过 GitHub Security Advisories 或私下联系维护者，不要在公开 Issue 中粘贴真实凭据。
