# API Key 配置

将申请的 API Key 写入对应的文件：

## 行情数据

1. **finnhub-key.md** — Finnhub API Key（美股行情）
   - 申请地址：https://finnhub.io/

## LLM（OpenAI 兼容格式）

5. **openai-key.md** — OpenAI 兼容 API Key
   - 用于 LLM 数据增强和深度分析
   - 支持任意 OpenAI 兼容服务（如 cliproxyapi 转发、Azure OpenAI、本地 Ollama 等）

6. **openai-base-url.md** — OpenAI 兼容 API Base URL（可选）
   - 例如：`https://api.cliproxyapi.com/v1`
   - 如使用官方 OpenAI 可省略此文件

**优先级**：命令行参数 `--openai-key` > 环境变量 `OPENAI_API_KEY` > `.secret/openai-key.md`

**环境变量方式**（不创建文件也可）：
```bash
export OPENAI_API_KEY="sk-xxx"
export OPENAI_BASE_URL="https://api.cliproxyapi.com/v1"
```

## 新闻数据（可选）

2. **gnews-key.md** — GNews API Key（英文新闻）
   - 申请地址：https://gnews.io/

3. **juhe-key.md** — 聚合数据 Key（中文新闻）
   - 申请地址：https://www.juhe.cn/

4. **juhe-caijing-key.md** — 聚合数据财经新闻 Key
   - 申请地址：https://www.juhe.cn/

---

每个文件只包含 Key/URL 字符串，例如：

```
abcd1234efgh5678ijkl9012mnop3456
```
