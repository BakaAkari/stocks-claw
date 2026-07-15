# API Key 配置

本目录只保存本机密钥和连接配置，已被 `.gitignore` 排除。每个文件只放实际值，不要添加代码块、说明文字或提交到 Git。

## 行情与情报

- `finnhub-key.md`:Finnhub API Key，对应环境变量 `FINNHUB_API_KEY`
- `polygon-key.md`:Polygon.io API Key，对应环境变量 `POLYGON_API_KEY`
- `gnews-key.md`:GNews API Key，对应环境变量 `GNEWS_API_KEY`
- `juhe-key.md` / `juhe-caijing-key.md`:可选聚合新闻 Key

FRED 当前使用官方 CSV 下载接口，不需要 API Key。

## OpenAI-compatible LLM

- `openai-key.md`:API Key
- `openai-base-url.md`:兼容 API Base URL

兼容环境变量:

- `OPENAI_COMPATIBLE_API_KEY` 或 `OPENAI_API_KEY`
- `OPENAI_COMPATIBLE_BASE_URL` 或 `OPENAI_BASE_URL`

配置优先级由具体调用路径决定，通常为显式参数或环境变量优先，其次读取 `.secret/` 文件。不得在文档、日志或 Git remote 中暴露真实值。

## HTTP

- `http-token`:非回环 HTTP 访问使用的 Bearer token

缺少必要配置时系统应在 `data_quality` 或明确错误中报告，不得伪装为成功。
