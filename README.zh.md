# OpenClaw / Hermes Personal Investment Advisor

基于 OpenClaw / Hermes 的个人投资顾问工具包，接入多源财经数据，利用 LLM 生成个性化投资建议。

**定位：Agent 能力扩展工具包** —— 部署在 Agent workspace 中，Agent 通过 CLI 命令调用功能。

## 快速开始

如果你是 AI Agent，请先阅读 [`AGENT_GUIDE.md`](AGENT_GUIDE.md)。

如果你是用户，请将本仓库交给你的 AI 助手，让它读取 `AGENT_GUIDE.md` 后协助你完成配置。

## 核心功能

- 📊 **资产管理** - 维护你的金融资产清单
- 📰 **新闻追踪** - 接入 Yahoo、GNews、聚合数据等多源财经新闻
- 📈 **行情分析** - 监控你关心的股票和 ETF
- 🤖 **AI 建议** - 基于 LLM 生成个人化投资建议
- 📲 **定时推送** - 通过 Agent 对话界面接收投资报告

## 系统要求

- Python 3.9+
- 可选：OpenClaw 或 Hermes Agent 运行环境
- Feishu 账号（可选，用于接收报告）

## 统一 CLI 入口

所有功能通过统一入口调用：

```bash
python3 -m stocks.cli.stocks <子命令> [选项]
```

### 子命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `query <代码>` | 查询股票/ETF 行情 | `python3 -m stocks.cli.stocks query 000300 --market sh` |
| `report` | 生成个人投资报告 | `python3 -m stocks.cli.stocks report --refresh-news` |
| `assets list` | 查看金融资产 | `python3 -m stocks.cli.stocks assets list --json` |
| `assets add` | 添加/更新资产 | `python3 -m stocks.cli.stocks assets add --name 黄金ETF --platform 支付宝 --amount 50000` |
| `health` | 系统健康检查 | `python3 -m stocks.cli.stocks health --json` |
| `news refresh` | 刷新新闻数据 | `python3 -m stocks.cli.stocks news refresh` |
| `config validate` | 校验配置 | `python3 -m stocks.cli.stocks config validate` |
| `logs` | 查看日志 | `python3 -m stocks.cli.stocks logs --lines 20` |

### `--json` 参数

所有子命令支持 `--json` 参数，以 JSON 格式输出，方便 Agent 解析：

```bash
python3 -m stocks.cli.stocks query 600519 --market sh --json
python3 -m stocks.cli.stocks health --json
python3 -m stocks.cli.stocks assets list --json
```

## 项目结构

```
.
├── AGENT_GUIDE.md          # ⭐ AI Agent 部署指南（必读）
├── README.md               # 英文版本
├── README.zh.md            # 本文件
├── requirements.txt        # Python 依赖
├── .secret/               # API Key 配置目录
│   ├── finnhub-key.md     # Finnhub API Key
│   ├── gnews-key.md       # GNews API Key
│   ├── juhe-key.md        # 聚合数据 Key（可选）
│   └── juhe-caijing-key.md # 聚合数据财经 Key（可选）
└── stocks/                # 核心代码
    ├── cli/               # 命令行工具（统一入口 stocks.py）
    ├── config/           # 配置文件
    ├── data/             # 资产数据（需自行填写）
    ├── services/         # 核心服务
    └── prompts/          # LLM 提示词
```

## 配置步骤

1. **申请 API Key**
   - [Finnhub](https://finnhub.io/)（美股行情）
   - [GNews](https://gnews.io/)（英文新闻）
   - [聚合数据](https://www.juhe.cn/)（中文新闻，可选）

2. **填写 API Key**
   ```bash
   echo "your-finnhub-key" > .secret/finnhub-key.md
   echo "your-gnews-key" > .secret/gnews-key.md
   ```

3. **配置资产和监控标的**
   ```bash
   # 编辑你的资产
   vim stocks/data/financial_assets.json
   
   # 编辑监控标的
   vim stocks/config/watchlist.json
   ```

4. **设置定时任务**（可选）
   ```bash
   # 系统 cron
   0 9,11,14,16 * * 1-5 cd /path/to/stocks-claw && python3 -m stocks.cli.stocks report --refresh-news --save
   ```

详细步骤请参考 [`AGENT_GUIDE.md`](AGENT_GUIDE.md)。

## 文档

- [`AGENT_GUIDE.md`](AGENT_GUIDE.md) - AI Agent 部署指南
- [`stocks/DATA_SOURCES.md`](stocks/DATA_SOURCES.md) - 数据源配置说明
- [`stocks/ARCHITECTURE.md`](stocks/ARCHITECTURE.md) - 系统架构设计
- [`stocks/DATA_MODEL.md`](stocks/DATA_MODEL.md) - 数据模型说明

## 免责声明

本系统仅供学习和参考，不构成投资建议。投资有风险，决策需谨慎。

## License

MIT License - 详见 [LICENSE](LICENSE)

---

*Built for OpenClaw / Hermes Agent*
