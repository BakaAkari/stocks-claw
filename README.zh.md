# OpenClaw Personal Investment Advisor

基于 OpenClaw 的个人投资顾问系统，接入多源财经数据，利用 LLM 生成个性化投资建议，并通过 Feishu 定时推送。

## 快速开始

如果你是 AI Agent，请先阅读 [`AGENT_GUIDE.md`](AGENT_GUIDE.md)。

如果你是用户，请将本仓库交给你的 AI 助手，让它读取 `AGENT_GUIDE.md` 后协助你完成配置。

## 核心功能

- 📊 **资产管理** - 维护你的金融资产清单
- 📰 **新闻追踪** - 接入 Yahoo、GNews、聚合数据等多源财经新闻
- 📈 **行情分析** - 监控你关心的股票和 ETF
- 🤖 **AI 建议** - 基于 LLM 生成个人化投资建议
- 📲 **定时推送** - 通过 Feishu 接收投资报告

## 系统要求

- [OpenClaw](https://github.com/openclaw/openclaw) 运行环境
- Python 3.11+
- Feishu 账号（用于接收报告）

## 项目结构

```
.
├── AGENT_GUIDE.md          # ⭐ AI Agent 部署指南
├── README.md               # 本文件
├── requirements.txt        # Python 依赖
├── .secret/               # API Key 配置目录
│   ├── README.md
│   ├── finnhub-key.md     # Finnhub API Key
│   ├── gnews-key.md       # GNews API Key
│   ├── juhe-key.md        # 聚合数据 Key（可选）
│   └── juhe-caijing-key.md # 聚合数据财经 Key（可选）
└── stocks/                # 核心代码
    ├── config/           # 配置文件
    ├── data/             # 资产数据（需自行填写）
    ├── cli/              # 命令行工具
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

4. **设置定时任务**
   ```bash
   openclaw cron add --name "stocks-report" \
     --cron "0 9,11,14,16 * * 1-5" \
     --session isolated \
     --message "bash stocks/scripts/personal-report-delivery.sh"
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

*Built with [OpenClaw](https://github.com/openclaw/openclaw)*
