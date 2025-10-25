# 使用官方 Python 環境
FROM python:3.11-slim

# 設定工作目錄
WORKDIR /app

# 複製本地檔案到容器
COPY . /app

# 安裝依賴
RUN pip install --no-cache-dir -r requirements.txt

# 設定環境變數
ENV PORT=8080

# 啟動 Flask 應用
CMD ["gunicorn", "-b", ":8080", "app:app"]
