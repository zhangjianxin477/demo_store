# Knowledge Hub

一个可自托管的个人知识工作台：把网页、Markdown、多格式文档和 MCP/Skill 产物统一沉淀，再通过混合检索、重排序、引用校验和受控 Agent 生成问答、主题综述、周报与邮件提醒。

> 你的文档、向量数据和 API Key 保留在你自己的部署环境中。

## 功能

- 浏览器插件采集网页正文、来源和图片；Markdown/MCP/文件三种知识入口。
- PDF、Word、Excel、PPT、HTML 等多格式解析、清洗和策略化分块。
- Milvus/Zilliz 向量检索 + BM25 关键词检索 + 可选 Reranker。
- 带文件路径和 chunk 证据的 RAG 问答；无证据时明确降级。
- 受控 Agent：检索、去重、归纳、写作、引用校验，输出 Markdown/PDF。
- 每日热点、每周主题综述、个性化提醒和 SMTP 邮件。

## Pipeline

```text
网页 / 文件 / MCP
  → 解析、清洗、版面感知分块
  → Embedding + Milvus/Zilliz，同时建立 BM25
  → 混合召回、去重、Reranker、引用上下文
  → 标准 RAG 或受控 Agent
  → Markdown / PDF / 文件资源 / 邮件
```

## 快速开始

要求：Docker 20.10+、Docker Compose v2+、至少 4GB 内存（远程模型模式）。

```bash
git clone <your-repository-url>
cd knowledge-hub
cp .env.example .env
```

在 `.env` 填入自己的模型 Key：

```env
OPENAI_API_KEY=你的LLM兼容接口Key
OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
OPENAI_MODEL=glm-4-flash

EMBEDDING_API_KEY=你的EmbeddingKey
EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_MODEL=Pro/BAAI/bge-m3
EMBEDDING_DIMENSION=1024
EMBEDDING_LOCAL_ENABLED=false
```

启动：

```bash
docker compose -f docker-compose.local.yml up -d --build
curl http://localhost:8080/api/v1/health
```

打开 `http://localhost:8080`。不要把 Key 写进前端代码或提交到 Git。

## 使用 Zilliz/Milvus

```env
VECTOR_BACKEND=milvus
MILVUS_URI=https://your-cluster.serverless.cloud.zilliz.com
MILVUS_TOKEN=你的ZillizToken
MILVUS_COLLECTION=knowledge_hub_chunks
MILVUS_METRIC_TYPE=COSINE
```

Embedding 维度必须和 Collection Schema 完全一致。更换模型或维度后，必须创建新 Collection 或全量重建向量，不能混用旧向量。

## 浏览器插件

1. 进入 `chrome://extensions`，开启开发者模式。
2. 点击“加载已解压的扩展程序”，选择 `browser-extension/`。
3. 在插件设置中填写你的实例地址，例如 `https://knowledge.example.com`。
4. 登录同一实例后采集网页；内容保存到 `自动抓取/YYYY-MM-DD/`。

## 云端部署

- `deploy/vps/`：单机 Docker + Caddy。
- `deploy/oracle/`：Oracle Free Tier 示例。
- `deploy/nginx/`：Nginx + HTTPS 模板。
- `deploy/tenants/`：多租户环境变量模板。

生产环境必须使用自己的域名、证书、随机 `SECRET_KEY`、持久化数据卷和严格 `CORS_ORIGINS`。详见 [DEPLOY.md](DEPLOY.md)。

## 评测

建议使用 `RAG_RETRIEVAL_EVAL_TEMPLATE.json` 准备 20～30 条人工标注 query，再运行 `evaluate_retrieval.py` 和 `evaluate_agent.py` 进行回归。

历史小样本（359 条、2560 维旧索引）结果：Vector Recall@5 80.0%，Hybrid Recall@5 100.0%，平均向量检索 53.5ms。该数据不代表当前 1024 维索引，模型切换后必须重新入库和评测。

## 安全

- `.gitignore` 排除 `.env`、数据、日志、虚拟环境、证书和备份。
- 密钥只通过服务器环境变量、GitHub Secrets 或 Secret Manager 注入。
- 如果 Key 曾出现在 Git、聊天、截图或日志中，请立即撤销并重新生成。
- 详见 [SECURITY.md](SECURITY.md)。

## 当前边界

这是个人/小团队可运行的 Agent RAG MVP，不承诺大规模高并发。复杂 PDF、动态网页、防盗链图片和 OCR 结果需要人工复核；Agent 默认生成草稿，外部发布必须走官方 API、授权和人工确认。

## License

MIT，详见 [LICENSE](LICENSE)。
