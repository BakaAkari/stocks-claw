# OpenClaw Personal Investment Advisor

A personal investment advisory system built on OpenClaw, integrating multi-source financial data, leveraging LLM to generate personalized investment advice, and delivering reports via Feishu on a scheduled basis.

> [中文版本](README.zh.md) | [Agent Guide](AGENT_GUIDE.md)

---

## Quick Start

If you are an AI Agent, please read [`AGENT_GUIDE.md`](AGENT_GUIDE.md) first.

If you are a user, please hand this repository to your AI assistant and let it read `AGENT_GUIDE.md` to help you complete the configuration.

---

## Core Features

- 📊 **Asset Management** - Maintain your financial asset portfolio
- 📰 **News Tracking** - Access Yahoo, GNews, Juhe and other multi-source financial news
- 📈 **Market Analysis** - Monitor stocks and ETFs you care about
- 🤖 **AI Advice** - Generate personalized investment advice based on LLM
- 📲 **Scheduled Delivery** - Receive investment reports via Feishu

---

## System Requirements

- [OpenClaw](https://github.com/openclaw/openclaw) runtime environment
- Python 3.11+
- Feishu account (for receiving reports)

---

## Installation Location

Place this repository in your OpenClaw workspace root directory:

```
/home/node/.openclaw/workspace/stocks-claw/
```

---

## Project Structure

```
.
├── AGENT_GUIDE.md              # ⭐ AI Agent deployment guide (must read)
├── README.md                   # This file
├── README.zh.md               # Chinese version
├── requirements.txt           # Python dependencies
├── .secret/                   # API Key configuration directory
│   ├── README.md
│   ├── finnhub-key.md         # Finnhub API Key
│   ├── gnews-key.md           # GNews API Key
│   ├── juhe-key.md            # Juhe Data Key (optional)
│   └── juhe-caijing-key.md    # Juhe Financial News Key (optional)
└── stocks/                    # Core code
    ├── config/               # Configuration files
    ├── data/                 # Asset data (fill in yourself)
    ├── cli/                  # Command line tools
    ├── services/             # Core services
    └── prompts/              # LLM prompts
```

---

## Configuration Steps

1. **Apply for API Keys**
   - [Finnhub](https://finnhub.io/) (US stock quotes)
   - [GNews](https://gnews.io/) (English news)
   - [Juhe Data](https://www.juhe.cn/) (Chinese news, optional)

2. **Fill in API Keys**
   ```bash
   echo "your-finnhub-key" > .secret/finnhub-key.md
   echo "your-gnews-key" > .secret/gnews-key.md
   ```

3. **Configure Assets and Watchlist**
   ```bash
   # Edit your assets
   vim stocks/data/financial_assets.json
   
   # Edit watchlist
   vim stocks/config/watchlist.json
   ```

4. **Set up Scheduled Tasks**
   ```bash
   openclaw cron add --name "stocks-report" \
     --cron "0 9,11,14,16 * * 1-5" \
     --session isolated \
     --message "bash stocks/scripts/personal-report-delivery.sh"
   ```

For detailed steps, please refer to [`AGENT_GUIDE.md`](AGENT_GUIDE.md).

---

## Documentation

- [`AGENT_GUIDE.md`](AGENT_GUIDE.md) - AI Agent deployment guide
- [`stocks/DATA_SOURCES.md`](stocks/DATA_SOURCES.md) - Data source configuration
- [`stocks/ARCHITECTURE.md`](stocks/ARCHITECTURE.md) - System architecture design
- [`stocks/DATA_MODEL.md`](stocks/DATA_MODEL.md) - Data model documentation

---

## Disclaimer

This system is for learning and reference purposes only and does not constitute investment advice. Investing involves risks; please make decisions cautiously.

---

## License

MIT License - See [LICENSE](LICENSE) for details

---

*Built with [OpenClaw](https://github.com/openclaw/openclaw)*
