FROM python:3.11-slim
RUN pip install --no-cache-dir "mcp==1.12.4"  # 钉 1.x：mcp 2.0 移除 mcp.server.fastmcp
COPY server.py /app/server.py
EXPOSE 8000
CMD ["python", "/app/server.py"]
