# 部署说明

## 后端 (backend/)

### 1. 环境变量

复制 `.env.example` 为 `.env`，**必须**设置以下项：

| 变量 | 说明 | 生产要求 |
|---|---|---|
| `APP_API_KEYS` | 逗号分隔的 API key。**空 = 接口完全开放** | **必填**。生成：`python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DATABASE_URL` | 数据库连接 | SQLite 单机可用；多机部署需改 PostgreSQL |
| `OCR_PROVIDER` | OCR 服务 | 生产用 `ppstructurev3` |
| `PPSTRUCTUREV3_URL` | PP-StructureV3 服务地址 | 按实际部署填写 |
| `LLM_PROVIDER` / `LLM_API_URL` / `LLM_API_KEY` | LLM 服务 | `LLM_API_URL` 以 `/v1` 结尾（SDK 自动追加 `/chat/completions`） |
| `ALLOWED_ORIGINS` | CORS 允许的前端源 | 前端不同源时必填 |
| `DEBUG` | 调试模式 | 生产设 `false`（控制 `reload` 与日志） |

### 2. 启动

```bash
# 推荐：用 uvicorn 直接启动（生产）
uvicorn app.main:app --host 0.0.0.0 --port 8010

# 或 python -m app.main（reload 由 DEBUG 控制，生产 DEBUG=false 不开 reload）
python -m app.main
```

启动时自动执行 `alembic upgrade head` 建表/迁移，**不再**用 `create_all`。

### 3. 鉴权

所有 `/api/v1/*` 路由需在请求头带 `X-API-Key: <key>`（或查询参数 `?api_key=<key>`，仅文件下载用）。`/health` 不鉴权。

### 4. 单实例限制

当前为**单实例部署**：SQLite + 进程内 worker。不支持水平扩展。如需多实例，需迁移到 PostgreSQL 并将 worker 独立部署 + 分布式锁。

---

## 前端 (frontend/)

### 1. 构建

```bash
npm install
npm run build   # 产物在 dist/
```

### 2. 连接后端

- **同源部署（推荐）**：前端与后端同源，由 nginx 反代 `/api/*` → `http://127.0.0.1:8010`。无需额外配置。
- **跨源部署**：复制 `.env.production.example` 为 `.env.production`，设置 `VITE_API_BASE_URL=https://<后端域名>`，并确保后端 `ALLOWED_ORIGINS` 包含前端源。

### 3. nginx 反代示例

```nginx
server {
    listen 80;
    server_name your.domain;

    root /path/to/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;   # OCR/LLM 耗时较长
    }
}
```
