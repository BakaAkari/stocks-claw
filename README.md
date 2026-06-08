# OpenClaw / Hermes Personal Investment Advisor

A personal investment advisory toolkit that integrates multi-source financial data and leverages LLM to generate personalized investment advice.

**Positioning: Agent Capability Extension Toolkit** — Deployed in the Agent workspace, the Agent invokes its functions through CLI commands.

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
- 📲 **Scheduled Delivery** - Receive investment reports through the Agent conversation interface

---

## System Requirements

- Python 3.9+
- Optional: OpenClaw or Hermes Agent runtime environment
- Feishu account (optional, for receiving reports)

---

## Unified CLI Entry

All functions are invoked through a unified entry point:

```bash
python3 -m stocks.cli.stocks <subcommand> [options]
```

### Subcommands

| Command | Description | Example |
|---------|-------------|---------|
| `query <code>` | Query stock/ETF quotes | `python3 -m stocks.cli.stocks query 000300 --market sh` |
| `report` | Generate personal investment report | `python3 -m stocks.cli.stocks report --refresh-news` |
| `assets list` | View financial assets | `python3 -m stocks.cli.stocks assets list --json` |
| `assets add` | Add/update asset | `python3 -m stocks.cli.stocks assets add --name GoldETF --platform Alipay --amount 50000` |
| `health` | System health check | `python3 -m stocks.cli.stocks health --json` |
| `news refresh` | Refresh news data | `python3 -m stocks.cli.stocks news refresh` |
| `config validate` | Validate configuration | `python3 -m stocks.cli.stocks config validate` |
| `logs` | View logs | `python3 -m stocks.cli.stocks logs --lines 20` |

### `--json` Flag

All subcommands support the `--json` flag for JSON output, making it easy for Agents to parse:

```bash
python3 -m stocks.cli.stocks query AAPL --market us --json
python3 -m stocks.cli.stocks health --json
python3 -m stocks.cli.stocks assets list --json
```

---

## Installation Location

Place this repository in your Agent workspace root directory:

```
/home/node/.openclaw/workspace/stocks-claw/
# or
~/hermes/workspace/stocks-claw/
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
│   ├── finnhub-key.md         # Finnhub API Key
│   ├── gnews-key.md           # GNews API Key
│   ├── juhe-key.md            # Juhe Data Key (optional)
│   └── juhe-caijing-key.md    # Juhe Financial News Key (optional)
└── stocks/                    # Core code
    ├── cli/                   # CLI tools (unified entry: stocks.py)
    ├── config/               # Configuration files
    ├── data/                 # Asset data (fill in yourself)
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

4. **Set up Scheduled Tasks** (optional)
   ```bash
   # System cron
   0 9,11,14,16 * * 1-5 cd /path/to/stocks-claw && python3 -m stocks.cli.stocks report --refresh-news --save
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

*Built for OpenClaw / Hermes Agent*
