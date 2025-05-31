FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 暴露端口为7860
EXPOSE 7860

# 启动服务
CMD ["python", "main.py"]
