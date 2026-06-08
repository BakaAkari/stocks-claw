# Agent 使用指南

本文档面向帮助用户部署和使用本系统的 AI Agent。

## 系统定位

**stocks-claw** 是一个**个人投资顾问工具包**，运行在 Agent 的 workspace 中。Agent 通过 CLI 命令调用其功能，为用户提供投资相关的查询、分析和报告服务。

**不是独立服务**，不需要启动 HTTP 服务或守护进程。

## 核心能力

Agent 可以通过 CLI 调用以下功能：

| 功能 | 命令 | 说明 |
|------|------|------|
| **查询行情** | `python3 -m stocks.cli.stocks query <代码>` | 查股票/ETF 实时价格 |
| **生成报告** | `python3 -m stocks.cli.stocks report` | 生成个人投资分析报告 |
| **查看资产** | `python3 -m stocks.cli.stocks assets list` | 查看用户金融资产 |
| **更新资产** | `python3 -m stocks.cli.stocks assets add ...` | 添加/修改资产 |
| **健康检查** | `python3 -m stocks.cli.stocks health` | 检查数据新鲜度 |
| **LLM 增强** | `python3 -m stocks.cli.stocks context --llm-enhancer` | 启用 LLM 数据增强 |
| **校验配置** | `python3 -m stocks.cli.stocks config validate` | 检查系统配置 |
| **查看日志** | `python3 -m stocks.cli.stocks logs` | 查看运行日志 |

## 快速开始

### 1. 确认项目位置

项目应在 Agent workspace 中：

```bash
cd /path/to/workspace/stocks-claw
```

### 2. 检查配置状态

```bash
python3 -m stocks.cli.stocks config validate
```

### 3. 查看用户资产

```bash
python3 -m stocks.cli.stocks assets list
```

### 4. 查询行情示例

```bash
# A股
python3 -m stocks.cli.stocks query 000300 --market sh

# 美股
python3 -m stocks.cli.stocks query AAPL --market us
```

### 5. 生成报告

```bash
python3 -m stocks.cli.stocks report --refresh-news --save
```

报告生成后会保存到 `stocks/reports/personal-latest.md`。

## CLI 详细说明

### 统一入口

所有功能通过统一入口调用：

```bash
python3 -m stocks.cli.stocks <子命令> [选项]
```

### 子命令详解

#### `query` - 查询行情

```bash
python3 -m stocks.cli.stocks query <代码> [--market <市场>] [--json]
```

参数：
- `code`: 证券代码（如 600519、000300、AAPL、QQQ）
- `--market`: 可选，市场代码（sh / sz / us）
- `--json`: 以 JSON 格式输出（供 Agent 解析）

示例：
```bash
python3 -m stocks.cli.stocks query 600519 --market sh
python3 -m stocks.cli.stocks query QQQ --market us --json
```

#### `report` - 生成个人投资报告

```bash
python3 -m stocks.cli.stocks report [--refresh-news] [--save] [--skip-dedup] [--model <模型>] [--fallback-model <模型>]
```

参数：
- `--refresh-news`: 生成前先刷新新闻
- `--save`: 保存报告到 `stocks/reports/`
- `--skip-dedup`: 跳过重复检测（强制生成）
- `--model`: 指定主 LLM 模型
- `--fallback-model`: 指定 fallback 模型

示例：
```bash
python3 -m stocks.cli.stocks report --refresh-news --save
```

#### `context` - 组装分析上下文（核心方法）

```bash
python3 -m stocks.cli.stocks context [--format json] [--llm-enhancer] [--detail <级别>]
```

参数：
- `--format`: 输出格式（json / markdown / text）
- `--llm-enhancer`: 启用 LLM 数据增强（摘要生成、跨源去重、质量分级）
- `--detail`: 输出粒度（compact / standard / full）
- `--no-news`: 不包含新闻
- `--no-quotes`: 不包含行情
- `--no-history`: 不包含历史快照

**示例 1：标准上下文（默认）**
```bash
python3 -m stocks.cli.stocks context --format json
```

返回 `AnalysisContext` JSON，包含资产、行情、新闻、脚手架等。

**示例 2：启用 LLM 增强**
```bash
python3 -m stocks.cli.stocks context --llm-enhancer --format json
```

返回的 JSON 中：
- `news` 中的每条新闻包含 `importance`、`urgency`、`category`、`sentiment` 标签
- `market_summary_nl` 字段包含行情自然语言摘要
- `llm_enhancer_enabled` 标记为 `true`

**示例 3：极简输出（token 紧张时使用）**
```bash
python3 -m stocks.cli.stocks context --detail compact --format json
```

只返回组合总览、关键偏离、3-5 条重要新闻标题。

#### `assets` - 资产管理

**查看资产：**
```bash
python3 -m stocks.cli.stocks assets list [--json]
```

**添加/更新资产：**
```bash
python3 -m stocks.cli.stocks assets add \
  --name "华安黄金ETF" \
  --platform "支付宝" \
  --amount 50000 \
  --type "黄金ETF" \
  --notes "定投"
```

参数：
- `--name`: 资产名称（必填）
- `--platform`: 平台/券商（必填）
- `--amount`: 持有金额（必填）
- `--type`: 资产类型（可选，默认 unknown）
- `--notes`: 备注（可选）

#### `health` - 健康检查

```bash
python3 -m stocks.cli.stocks health [--json]
```

检查项：
- 行情数据新鲜度（30 分钟阈值）
- 新闻数据新鲜度（2 小时阈值）
- 市场状态数据新鲜度（1 小时阈值）
- 最新报告是否存在

#### `news` - 新闻操作

```bash
python3 -m stocks.cli.stocks news refresh [--limit <数量>] [--json]
```

#### `config` - 配置操作

```bash
python3 -m stocks.cli.stocks config validate [markets...] [--json]
```

#### `logs` - 查看日志

```bash
python3 -m stocks.cli.stocks logs [--lines <数量>] [--json]
```

## 配置文件

### 用户资产

`stocks/data/financial_assets.json`

用户金融资产清单，Agent 可以帮用户维护：

```json
{
  "schema_version": 1,
  "updated_at": "2026-01-01 00:00:00",
  "assets": [
    {
      "asset_name": "货币基金",
      "platform": "支付宝",
      "amount": 100000,
      "asset_type": "现金管理",
      "notes": "活期储备",
      "confirmed_by_user": true
    }
  ],
  "portfolio_constraints": {
    "target_bucket_ranges": {
      "growth_total": {"min": 0.10, "max": 0.30}
    },
    "max_drawdown_tolerance": 0.15,
    "allow_stop_loss": false
  },
  "portfolio_profile_notes": {
    "investment_preference": "稳健偏成长",
    "portfolio_focus": "关注组合健康程度"
  }
}
```

### 监控标的

`stocks/config/watchlist.json`

用户关心的股票/ETF 列表：

```json
{
  "markets": {
    "a": {
      "label": "A股",
      "watchlist": [
        {"code": "000300", "name": "沪深300", "market": "sz_index"},
        {"code": "518880", "name": "华安黄金ETF", "market": "sh"}
      ]
    },
    "us": {
      "label": "美股",
      "watchlist": [
        {"code": "QQQ", "name": "纳斯达克100ETF", "market": "us"},
        {"code": "AAPL", "name": "AAPL", "market": "us"}
      ]
    }
  }
}
```

### API Key

`.secret/` 目录：

```bash
.secret/
├── finnhub-key.md       # Finnhub API Key（美股行情）
├── gnews-key.md         # GNews API Key（英文新闻）
├── juhe-key.md          # 聚合数据 Key（中文新闻，可选）
└── juhe-caijing-key.md  # 聚合数据财经 Key（可选）
```

每个文件只包含 Key 字符串。

## Agent 工作流示例

### 场景 1：用户查询股票

用户："紫金矿业今天怎么样？"

Agent 执行：
```bash
python3 -m stocks.cli.stocks query 601899 --market sh --json
```

解析 JSON 输出，回复用户：
> 紫金矿业 (601899) 最新价 12.50 元，涨 2.46%

### 场景 2：用户查看资产

用户："我现在的资产情况？"

Agent 执行：
```bash
python3 -m stocks.cli.stocks assets list --json
```

解析后回复用户资产概况。

### 场景 3：用户更新资产

用户："我最近在支付宝买了 5 万块黄金 ETF"

Agent 执行：
```bash
python3 -m stocks.cli.stocks assets add \
  --name "华安黄金ETF" \
  --platform "支付宝" \
  --amount 50000 \
  --type "黄金ETF"
```

确认后回复用户。

### 场景 4：生成投资报告

用户："帮我看看今天的投资建议"

Agent 执行：
```bash
python3 -m stocks.cli.stocks report --refresh-news --save
```

读取生成的报告文件 `stocks/reports/personal-latest.md`，整理后回复用户。

### 场景 5：定时报告（cron）

通过系统 cron 定时执行：

```bash
# 交易日 9:00、11:00、14:00、16:00
0 9,11,14,16 * * 1-5 cd /path/to/stocks-claw && python3 -m stocks.cli.stocks report --refresh-news --save
```

Agent 可以在对话中告知用户报告已生成，或主动推送报告摘要。

### 场景 6：使用 LLM 数据增强获取高质量上下文

用户："帮我分析一下今天的市场，特别关注重要新闻"

**方式 A：Agent 自己分析（推荐，使用增强数据）**

Agent 执行：
```bash
python3 -m stocks.cli.stocks context --llm-enhancer --format json
```

返回的 JSON 中：
- `news` 包含 `importance`、`urgency`、`category`、`sentiment` 标签
- `market_summary_nl` 包含行情自然语言摘要
- Agent 可以基于 `importance=high` 筛选重要新闻，自己分析后回复用户

**方式 B：让 stocks-claw 内部生成报告（兼容模式）**

Agent 执行：
```bash
python3 -m stocks.cli.stocks report --llm-enhancer --save
```

stocks-claw 内部 LLM 基于增强后的数据生成报告。

**LLM Enhancer 的优势：**
- Juhe 源新闻（无摘要）会自动生成摘要
- 跨源重复新闻（如"美联储加息"在 RSS 和 GNews 都有报道）会自动去重
- 新闻按 `importance` 和 `urgency` 分级，Agent 可以优先处理高优先级新闻
- 行情数据有自然语言摘要，Agent 可以直接引用

### 场景 7：快速判断（token 紧张时使用）

用户："今天市场怎么样？简单说说"

Agent 执行：
```bash
python3 -m stocks.cli.stocks context --detail compact --llm-enhancer --format json
```

返回极简上下文（~500 token），Agent 快速回复用户。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `STOCKS_LLM_ENHANCER_ENABLED` | `false` | 是否启用 LLM 数据增强 |
| `STOCKS_LLM_ENHANCER_MODEL` | `gpt-4o-mini` | 数据增强模型（低成本） |
| `STOCKS_LLM_MODEL` | `gpt-5.4` | 主 LLM 模型（报告生成） |
| `STOCKS_FALLBACK_LLM_MODEL` | `kimi-k2.5` | Fallback 模型 |
| `STOCKS_LLM_URL` | `http://localhost:11434/v1/chat/completions` | LLM API 端点 |
| `STOCKS_LLM_API_KEY` | `''` | LLM API Key |

## 故障排查

### 行情查询失败
1. 检查网络连接
2. A股使用腾讯财经接口，无需 Key
3. 美股需要 Finnhub Key

### 报告生成失败
1. 检查 LLM 配置（URL、Key、模型名）
2. 检查 API Key 是否配置
3. 查看日志：`python3 -m stocks.cli.stocks logs --lines 20`

### 新闻数据为空
1. 检查 GNews/聚合数据 Key 是否有效
2. 检查 API 配额

## 限制说明

- **不是自动交易系统**：只提供建议，不执行交易
- **需要人工确认**：资产更新需用户确认后才写入
- **API 配额限制**：免费额度有限

---

*最后更新：2026-06-05*
