# cloudpivot

## React 前端 API

在后端 `.env` 配置默认模型、MinIO 和 CloudPivot 专用 PostgreSQL。数据库连接支持完整
PostgreSQL URL，也支持 `CLOUDPIVOT_DATABASE_URL=主机:端口` 与独立的
`CLOUDPIVOT_DATABASE_NAME`、`CLOUDPIVOT_DATABASE_USER`、`CLOUDPIVOT_DATABASE_PASSWORD`。
FastAPI 启动时通过 SQLAlchemy 初始化业务表，通过 LangGraph PostgreSQL Saver 初始化检查点表。
Redis 可选，可用 `主机:端口` 或 `redis://主机:端口/1`。缓存只使用 DB 1。

在后端目录安装依赖，然后交互式创建首个运营账号。登录后从工作台进入「用户邀请」生成邀请链接。

```powershell
uv sync --locked
uv run --locked python -m api.manage create-operator operator
```

本机 HTTP 联调时将 `CLOUDPIVOT_COOKIE_SECURE` 设为 `false`，HTTPS 部署使用默认值 `true`。
启动 FastAPI，使用一个应用进程连接对应的 PostgreSQL schema。

```powershell
uv run --locked python main.py
```

在 PyCharm 中选择项目的 `.venv` 解释器，直接运行根目录的 `main.py`。服务默认监听
`127.0.0.1:8000`，可在运行配置的环境变量中设置 `UVICORN_PORT` 更换端口。

`GET /api/health` 返回 `{"status":"ok"}`，用于验证浏览器能访问 FastAPI。
留空数据库配置时可单独验证连通性。前端默认通过同源 `/api` 访问，推荐由前端反向代理。
同一站点使用独立 API 子域名时，设置 `CLOUDPIVOT_CORS_ORIGINS` 为允许的前端源，
例如 `https://app.example.com`。多个源使用逗号分隔。

后端测试使用 `uv run --locked python -m unittest discover -s tests -v`。
设置 `CLOUDPIVOT_TEST_DATABASE_URL` 启用真实 PostgreSQL 集成测试，连接账号须能创建临时 schema。
可设置 `CLOUDPIVOT_TEST_REDIS_URL` 以同时测试 Redis DB 1，使用以 `/1` 结尾的 Redis URL。

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
