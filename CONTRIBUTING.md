# Contributing

欢迎提交 bug 修复、文档改进和评测用例。

## 提交前检查

```bash
python -m pytest backend/tests -q
python -m py_compile backend/app/main.py backend/app/api/routes.py
node --check frontend/js/app.js
node --check browser-extension/background.js
```

请不要提交密钥、用户数据、日志、虚拟环境或本地模型缓存。新增检索能力时，请同时补充可复现的评测 query 和 badcase。
