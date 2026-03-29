# Agent 使用指南

本文档面向帮助用户部署和使用本系统的 AI Agent。

---

## 系统概述

这是一个运行在 OpenClaw 内的 **Personal Investment Advisor**（个人投资顾问）。

核心功能：
- 维护用户金融资产信息
- 接入市场新闻和行情
- 定时生成个人化投资建议
- 通过 Feishu 投递报告

---

## 技术依赖

**必需**：
- OpenClaw 运行时环境
- Python 3.11+
- Feishu 账号（用于接收报告）

**外部 API**（需用户自行申请）：
- Finnhub（美股行情）
- GNews（英文新闻）
- 聚合数据（中文新闻，可选）

---

## 部署流程

### Step 0: 安装位置

将本仓库克隆到 OpenClaw 工作空间的根目录：

```bash
cd /home/node/.openclaw/workspace
git clone https://github.com/BakaAkari/stocks-claw.git
cd stocks-claw
```

### Step 1: 配置文件初始化

```bash
# 1. 复制配置文件模板
cp stocks/config/example-watchlist.json stocks/config/watchlist.json
cp stocks/config/example-financial_assets.json stocks/data/financial_assets.json

# 2. 创建 API Key 文件
touch .secret/finnhub-key.md
touch .secret/gnews-key.md
touch .secret/juhe-key.md
touch .secret/juhe-caijing-key.md
```

### Step 2: 填写 API Key

指导用户到以下网站申请 Key：
- Finnhub: https://finnhub.io/
- GNews: https://gnews.io/
- 聚合数据: https://www.juhe.cn/

将 Key 写入对应的 `.secret/*-key.md` 文件。

### Step 3: 配置监控标的

编辑 `stocks/config/watchlist.json`：
- A股：用户关心的股票/ETF
- 美股：用户持有的美股标的

### Step 4: 录入初始资产

编辑 `stocks/data/financial_assets.json`：
- 录入用户的现金、理财、基金、股票、黄金等资产
- 填写 `confirmed_by_user: true` 标记已确认资产

### Step 5: 设置定时任务

```bash
# 交易日推送（每日4次）
openclaw cron add --name "stocks-report-trading" \
  --cron "0 9,11,14,16 * * 1-5" --tz "Asia/Shanghai" \
  --session isolated --model gpt-5.4 \
  --message "bash /path/to/personal-report-delivery.sh"

# 非交易日推送（每日2次）
openclaw cron add --name "stocks-report-weekend" \
  --cron "0 10,16 * * 0,6" --tz "Asia/Shanghai" \
  --session isolated --model gpt-5.4 \
  --message "bash /path/to/personal-report-delivery.sh"
```

---

## 日常运维

### 资产更新

当用户进行买卖操作时，更新 `stocks/data/financial_assets.json`：

```json
{
  "asset_name": "资产名称",
  "platform": "平台/券商",
  "amount": 金额,
  "asset_type": "类型",
  "notes": "备注",
  "confirmed_by_user": true
}
```

### 健康检查

```bash
python3 stocks/cli/health_check.py
```

检查项：
- 行情数据是否过期
- 新闻数据是否过期
- API Key 是否有效

### 手动刷新数据

```bash
# 刷新行情
python3 stocks/services/market_data_service.py

# 刷新新闻
python3 stocks/services/news_fetch_service.py
```

---

## 报告生成流程

1. **触发**：OpenClaw cron 或手动执行
2. **数据收集**：刷新新闻、行情
3. **分析生成**：LLM 基于资产+市场数据生成建议
4. **格式处理**：脱敏、格式化
5. **投递**：推送到 Feishu

---

## 故障排查

### 行情数据不更新

1. 检查 Finnhub Key 是否过期
2. 检查网络连接
3. 手动执行刷新测试

### 新闻数据为空

1. 检查 GNews/聚合数据 Key 是否有效
2. 检查 API 配额是否用完

### 报告未推送

1. 检查 Feishu target ID 是否正确
2. 检查 OpenClaw cron 状态
3. 检查冷却去重机制（60分钟内不重复推送相似内容）

---

## 核心文件说明

```
stocks/
├── config/
│   ├── markets.json          # 行情数据源配置
│   ├── news_sources.json     # 新闻数据源配置
│   └── watchlist.json        # 监控标的列表
├── data/
│   └── financial_assets.json # 用户金融资产（需用户填写）
├── prompts/
│   └── personal_advice_prompt.txt  # LLM 提示词
├── services/                 # 核心服务代码
├── cli/                      # 命令行工具
└── reports/                  # 生成报告（自动创建）
```

---

## 限制说明

- **不是自动交易系统**：只提供建议，不执行交易
- **依赖 OpenClaw**：无法独立运行
- **需要人工确认**：资产更新需用户确认后才写入
- **API 配额限制**：免费额度有限，超限需付费

---

## 自定义扩展

如需修改报告风格或分析维度：

1. 编辑 `stocks/prompts/personal_advice_prompt.txt`
2. 修改风格要求、输出格式、分析重点
3. 测试生成效果

---

*最后更新：2026-03-29*
