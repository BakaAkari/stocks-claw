FROM python:3.11-slim

WORKDIR /app

# 复制项目代码
COPY stocks/ ./stocks/
COPY config/ ./config/
COPY data/ ./data/

# 暴露 HTTP 端口
EXPOSE 8687

# 启动 HTTP 服务
# 监听 0.0.0.0 允许 Docker 外部访问
CMD ["python", "-m", "stocks.adapters.http", "--host", "0.0.0.0", "--port", "8687", "--llm-enhancer", "--llm-analysis"]
