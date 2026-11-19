# cloudpivot

## React 前端 API

在后端目录启动 FastAPI。

```powershell
uv run --locked python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

`GET /api/health` 返回 `{"status":"ok"}`，用于验证浏览器能访问 FastAPI。
该接口单独启动，无需模型、数据库或 MinIO。前端默认通过同源 `/api` 访问。
独立域名部署时，在启动 FastAPI 前设置 `CLOUDPIVOT_CORS_ORIGINS` 为允许的前端源，
例如 `https://app.example.com`。多个源使用逗号分隔。

## 会话工作区

在 LangGraph Studio 或 Agent Chat UI 中创建会话后发送消息。直接调用图时，请传入
`config={"configurable": {"thread_id": "<会话 ID>"}}`。继续使用同一个 ID 可以读取该会话已保存的文件。

工作区默认存放在 `/agent_files/sessions-v1/<thread_id 的 SHA-256>`，可通过
`AGENT_WORKSPACE_ROOT` 指定父目录。Agent 使用的虚拟路径仍从 `/` 开始。上传、生成文件、
自定义 Skill、Excel 和下载归档都使用当前会话目录。内置 Skill 从只读的 `/builtin-skills/` 加载。

升级前的共享文件保留在原目录。需要恢复旧文件时，确认归属后将对应文件上传到目标会话。
部署多个实例时，为工作区配置共同的持久卷。对外提供多用户服务时，在 LangGraph API 配置身份验证
和线程所有权校验，确保调用者拥有当前线程的访问权限。

Windows 上如果 `langgraph dev` 遇到 OpenAPI 文档的 GBK 解码错误，可在 PowerShell 启动前设置
`$env:PYTHONUTF8 = "1"`。
