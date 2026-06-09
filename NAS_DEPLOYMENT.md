# stocks-claw NAS Docker 部署指南（Hermes Agent 接入版）

> 目标：在 Unraid NAS 上通过 Docker 部署 stocks-claw HTTP 服务（端口 **8687**），供 Hermes Agent（飞书入口）调用。

---

## 架构概览

```
飞书用户消息
↓
Hermes Agent (Docker @ NAS)
↓ HTTP API 调用 (端口 8687)
stocks-claw (Docker @ NAS)
↓
行情/新闻/分析数据
↓
LLM Proxy (NAS 本地:8317)
↓
投资分析报告
```

---

## 前置条件

| 组件 | 状态 | 说明 |
|------|------|------|
| Unraid NAS | 已有 | 运行 Docker |
| Hermes Agent | 已有 | Docker 部署，飞书入口 |
| LLM Proxy | 已有 | NAS 本地 `:8317`，OpenAI 兼容 |
| stocks-claw 代码 | ⬜ 需复制 | 从 MacBook 复制到 NAS |

---

## 快速部署（3 步）

### Step 1: 准备 NAS 目录

在 Unraid 终端执行：

```bash
# 创建目录结构
mkdir -p /mnt/user/appdata/stocks-claw/{config,data,.local,.secret}

# 复制配置文件（从 MacBook 通过 SMB/SCP 复制）
cp /path/to/stocks-claw/stocks/config/* /mnt/user/appdata/stocks-claw/config/
cp /path/to/stocks-claw/stocks/data/* /mnt/user/appdata/stocks-claw/data/

# 复制隐私数据（真实资产）
cp /path/to/stocks-claw/.local/financial_assets.json /mnt/user/appdata/stocks-claw/.local/

# 创建 secret 文件
echo "your-finnhub-key" > /mnt/user/appdata/stocks-claw/.secret/finnhub-key.md
echo "your-openai-key" > /mnt/user/appdata/stocks-claw/.secret/openai-key.md
# LLM Proxy 在 NAS 本地，容器内通过宿主机网络访问
echo "http://host.docker.internal:8317/v1" > /mnt/user/appdata/stocks-claw/.secret/openai-base-url.md
```

### Step 2: 创建 Dockerfile

在 `/mnt/user/appdata/stocks-claw/` 创建 `Dockerfile`：

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 复制项目代码
COPY stocks/ ./stocks/
COPY config/ ./config/
COPY data/ ./data/

# 暴露 HTTP 端口
EXPOSE 8687

# 启动 HTTP 服务
CMD ["python", "-m", "stocks.adapters.http", "--host", "0.0.0.0", "--port", "8687", "--llm-enhancer", "--llm-analysis"]
```

### Step 3: 通过 Unraid Docker UI 部署

1. 打开 **Docker → Add Container**
2. 选择 **User Defined**
3. 配置：

| 字段 | 值 |
|------|-----|
| Name | `stocks-claw` |
| Repository | 留空（使用本地 build） |
| Network Type | `Bridge` |
| Port | `8687 → 8687` |
| Path 1 | `/mnt/user/appdata/stocks-claw/config → /app/config` |
| Path 2 | `/mnt/user/appdata/stocks-claw/data → /app/data` |
| Path 3 | `/mnt/user/appdata/stocks-claw/.local → /app/.local` |
| Path 4 | `/mnt/user/appdata/stocks-claw/.secret → /app/.secret` |
| Extra Parameters | `--add-host=host.docker.internal:host-gateway` |

4. 点击 **Apply**

**或使用 docker-compose（需 Compose.Manager 插件）：**

```yaml
version: "3.8"

services:
stocks-claw:
build: /mnt/user/appdata/stocks-claw
container_name: stocks-claw
ports:
- "8687:8687"
volumes:
- /mnt/user/appdata/stocks-claw/config:/app/config
- /mnt/user/appdata/stocks-claw/data:/app/data
- /mnt/user/appdata/stocks-claw/.local:/app/.local
- /mnt/user/appdata/stocks-claw/.secret:/app/.secret
extra_hosts:
- "host.docker.internal:host-gateway"
restart: unless-stopped
networks:
- stocks-network

networks:
stocks-network:
driver: bridge
name: stocks-claw-network
```

---

## 验证部署

```bash
# 在 NAS 上测试
curl http://localhost:8687/api/v1/health

# 预期返回
{
"success": true,
"data": {
"status": "ok",
"providers": ["tencent_a", "eastmoney_a", "finnhub"],
"assets_loaded": 7,
"watchlist_loaded": 11,
"llm_enhancer_enabled": true,
"llm_analysis_enabled": true
}
}
```

---

## Hermes Agent 接入方案

### 方案 A: HTTP API + Shell Skill（推荐，最简单）

Hermes Agent 内置 **Shell 工具**，可以直接 curl 调用 stocks-claw。

在 Hermes CLI 中创建 Skill：

```
你：帮我创建一个 Skill，命名为 "stocks-analysis"。
功能：调用 http://host.docker.internal:8687 获取投资分析数据。
支持 4 种查询：
1) portfolio — 获取组合摘要
2) quotes — 获取行情
3) news — 获取新闻
4) report — 获取完整投资报告

用 curl 调用，JSON 输出，中文回复。
```

Hermes 会自动生成类似这样的 Skill：

```python
# ~/.hermes/skills/stocks-analysis/procedure.py
import subprocess, json

BASE_URL = "http://host.docker.internal:8687"

def get_portfolio():
result = subprocess.run(
["curl", "-s", "-X", "POST", f"{BASE_URL}/api/v1/portfolio/summary",
"-H", "Content-Type: application/json", "-d", "{}"],
capture_output=True, text=True
)
return json.loads(result.stdout)

def get_quotes(market=None):
body = json.dumps({"market": market}) if market else "{}"
result = subprocess.run(
["curl", "-s", "-X", "POST", f"{BASE_URL}/api/v1/quotes",
"-H", "Content-Type: application/json", "-d", body],
capture_output=True, text=True
)
return json.loads(result.stdout)

def get_news(limit=5):
body = json.dumps({"limit": limit})
result = subprocess.run(
["curl", "-s", "-X", "POST", f"{BASE_URL}/api/v1/news",
"-H", "Content-Type: application/json", "-d", body],
capture_output=True, text=True
)
return json.loads(result.stdout)

def get_report():
body = json.dumps({"include_news": True, "include_quotes": True})
result = subprocess.run(
["curl", "-s", "-X", "POST", f"{BASE_URL}/api/v1/analysis/context",
"-H", "Content-Type: application/json", "-d", body],
capture_output=True, text=True
)
return json.loads(result.stdout)
```

**触发方式**：
- 飞书用户发送："看看我的持仓"
- Hermes 识别意图 → 调用 `stocks-analysis` Skill → curl → stocks-claw → 格式化回复

### 方案 B: MCP 接入（如果 Hermes 启用了 MCP）

Hermes Agent **原生支持 MCP 协议**。stocks-claw 已内置 MCPAdapter。

在 Hermes 中配置 MCP Server：

```bash
# 在 Hermes 配置中添加 MCP Server
hermes tools add mcp-server \
--name stocks-claw \
--command "docker exec stocks-claw python -m stocks.adapters.mcp --llm-enhancer --llm-analysis"
```

或配置为 HTTP MCP（如果 Hermes 支持）：

```json
{
"mcpServers": {
"stocks-claw": {
"url": "http://host.docker.internal:8687/mcp"
}
}
}
```

> ️ 注意：当前 stocks-claw MCPAdapter 使用 stdio 传输，在 Docker 跨容器场景中需要改为 SSE/HTTP 传输。如需此方案，需额外开发 MCP HTTP 传输层。

### 方案 C: 直接 HTTP Webhook（如果 Hermes 支持自定义 HTTP 节点）

在 Hermes 的 Gateway/Flow 配置中，添加一个 HTTP 调用节点：

```
触发词: "持仓|行情|新闻|分析报告"
动作: POST http://host.docker.internal:8687/api/v1/analysis/context
响应: 解析 JSON → 格式化 Markdown → 回复飞书
```

---

## HTTP API 端点清单

| 端点 | 方法 | 说明 | 示例请求体 |
|------|------|------|-----------|
| `/api/v1/health` | GET | 健康检查 | — |
| `/api/v1/portfolio/summary` | POST | 组合摘要 | `{}` |
| `/api/v1/quotes` | POST | 行情数据 | `{"market": "a"}` 或 `{}` |
| `/api/v1/news` | POST | 新闻数据 | `{"limit": 5}` |
| `/api/v1/analysis/context` | POST | 完整上下文（含 LLM 报告） | `{"include_news": true, "include_quotes": true}` |

### 请求示例

**获取组合摘要（飞书用户说"看看我的持仓"）：**
```bash
curl -s -X POST http://host.docker.internal:8687/api/v1/portfolio/summary \
-H "Content-Type: application/json" -d '{}'
```

**获取完整投资报告（飞书用户说"生成投资报告"）：**
```bash
curl -s -X POST http://host.docker.internal:8687/api/v1/analysis/context \
-H "Content-Type: application/json" \
-d '{"include_news": true, "include_quotes": true, "include_history": true}'
```

> ️ `analysis/context` 端点会调用 LLM 生成报告，响应时间 20-60s，需在 Hermes 中配置超时。

---

## 关键配置检查清单

部署前请确认以下事项：

### 1. LLM Proxy 可达性 最关键

stocks-claw 容器需要能访问 NAS 上的 LLM Proxy (`:8317`)。

**验证方式：**
```bash
# 进入 stocks-claw 容器
docker exec -it stocks-claw bash

# 测试 LLM Proxy 连通性
curl http://host.docker.internal:8317/v1/models
```

**如果无法访问**，检查：
- Unraid 网络设置 → Docker 是否启用 `host.docker.internal`
- 或改用 NAS 实际内网 IP（如 `http://192.168.1.xxx:8317/v1`）
- 或改用 `network_mode: host`（不推荐，会损失容器隔离性）

### 2. 隐私数据挂载

确认 `.local/financial_assets.json` 已正确挂载到容器：

```bash
docker exec stocks-claw cat /app/.local/financial_assets.json
```

### 3. API Key 文件

确认 secret 文件已挂载：

```bash
docker exec stocks-claw ls -la /app/.secret/
```

### 4. 时区设置 ⬜ 建议配置

在 Dockerfile 或 docker-compose 中添加时区，确保报告时间正确：

```dockerfile
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
```

### 5. 定时任务 ⬜ 可选

如需定时推送报告，可在 Unraid 的 **User Scripts** 插件中配置：

```bash
#!/bin/bash
# 每日 9:00 生成报告并推送到飞书（需 Hermes 支持）
REPORT=$(curl -s -X POST http://localhost:8687/api/v1/analysis/context \
-H "Content-Type: application/json" \
-d '{"include_news": true, "include_quotes": true}')
# 通过 Hermes API 或飞书 Webhook 推送
```

---

## 故障排查

| 问题 | 排查命令 |
|------|----------|
| 容器启动失败 | `docker logs stocks-claw` |
| API 返回空资产 | `docker exec stocks-claw cat /app/.local/financial_assets.json` |
| 行情获取失败 | `docker exec stocks-claw cat /app/.secret/finnhub-key.md` |
| LLM 报告失败 | `docker exec stocks-claw curl -v http://host.docker.internal:8317/v1/models` |
| Hermes 无法连接 | `docker exec hermes curl -v http://host.docker.internal:8687/api/v1/health` |
| 端口冲突 | `netstat -tlnp \| grep 8687` |

---

## 已知限制 & 待确认问题

1. **MCP 传输协议**：当前 MCPAdapter 使用 stdio，跨 Docker 容器需改为 SSE/HTTP。如需原生 MCP 接入，需额外开发。

2. **Hermes Skill 持久化**：Skill 文件存储在 Hermes 容器内，重启后可能丢失。建议挂载到宿主机卷。

3. **LLM 报告超时**：`analysis/context` 端点调用 LLM 需 20-60s，Hermes 默认超时可能不够。需在 Hermes 配置中增加 tool 调用超时。

4. **并发限制**：Finnhub 免费版 60 calls/min，多用户同时查询可能触发限流。

5. **报告截断**：飞书单条消息有长度限制，长报告需分页或生成图片/文件。

---

## 下一步行动

1. ⬜ 将 stocks-claw 代码复制到 NAS `/mnt/user/appdata/stocks-claw/`
2. ⬜ 复制 `.local/financial_assets.json` 和 `.secret/*` 到 NAS
3. ⬜ 在 NAS 上 build & run Docker 容器
4. ⬜ 验证 `curl http://localhost:8687/api/v1/health`
5. ⬜ 验证 LLM Proxy 连通性（`docker exec stocks-claw curl http://host.docker.internal:8317/v1/models`）
6. ⬜ 在 Hermes 中创建 `stocks-analysis` Skill
7. ⬜ 测试飞书 → Hermes → stocks-claw 完整链路

---

*端口: 8687 | 协议: HTTP JSON API | 容器: Docker | 接入: Hermes Agent Skill*
