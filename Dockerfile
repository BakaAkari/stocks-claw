FROM python:3.11-slim

WORKDIR /app

# 设置时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY stocks/ ./stocks/

# 创建隐私数据目录（实际数据通过卷挂载）
RUN mkdir -p /app/.local /app/.secret

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8687/api/v1/health', timeout=5)" || exit 1

# 暴露 HTTP 端口
EXPOSE 8687

# 启动 HTTP 服务（默认禁用内部 LLM，由 Agent 主脑做分析）
CMD ["python", "-m", "stocks.adapters.http", "--host", "0.0.0.0", "--port", "8687"]
