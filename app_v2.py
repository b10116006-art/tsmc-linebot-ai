import os
import re
import csv
import io
import traceback
import base64
from datetime import datetime
from flask import Flask, request, jsonify
from pymongo import MongoClient
from linebot import LineBotApi, WebhookHandler
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# ---------------------------------------
# 🔧 Flask 初始化
# ---------------------------------------
app = Flask(__name__)

# ---------------------------------------
# 🔧 環境變數
# ---------------------------------------
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = "tsmc_ai"
MONGO_COL = "lot_scrap_records"

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise RuntimeError("LINE token/secret 尚未在環境變數設定")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI 尚未設定")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB]
col = db[MONGO_COL]

# ---------------------------------------
# 🧩 上傳 CSV
# ---------------------------------------
@app.route("/admin/upload_csv", methods=["POST"])
def upload_csv():
    try:
        if "file" not in request.files:
            return jsonify({"error": "no file uploaded"}), 400

        file = request.files["file"]
        if not file.filename.endswith(".csv"):
            return jsonify({"error": "only CSV accepted"}), 400

        stream = io.StringIO(file.stream.read().decode("utf-8"))
        reader = csv.DictReader(stream)
        data = list(reader)

        if not data:
            return jsonify({"error": "CSV is empty"}), 400

        for row in data:
            for k, v in row.items():
                if isinstance(v, str) and v.strip().isdigit():
                    row[k] = int(v)
        if data:
            col.insert_many(data)

        return jsonify({"inserted": len(data)})

    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ---------------------------------------
# 🧩 LINE Webhook
# ---------------------------------------
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except Exception as e:
        print("Webhook Error:", e)
    return "OK"


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text.strip()
    reply_text = handle_query(user_text)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))


# ---------------------------------------
# 🧩 查詢核心邏輯
# ---------------------------------------
def handle_query(user_text: str) -> str:
    match = re.search(r"(M\d+)", user_text)
    step = match.group(1) if match else None

    query = {}
    if step:
        query["Step"] = step

    docs = list(col.find(query).limit(5))
    if not docs:
        return f"找不到 {step or '相關'} 的報廢紀錄"

    summary = []
    for d in docs:
        summary.append(
            f"{d.get('Lot ID','?')} / {d.get('Defect Type','?')} / {d.get('Date','?')}"
        )

    return f"找到 {len(docs)} 筆 {step or '相關'} 的報廢資料：\n" + "\n".join(summary)


# ---------------------------------------
# 🧩 Debug: 查詢端點（Base64 安全）
# ---------------------------------------
@app.route("/debug/query", methods=["POST"])
def debug_query():
    try:
        data = request.get_json(force=True)
        raw_text = data.get("text", "").strip()

        # 支援 Base64 中文
        try:
            user_text = base64.b64decode(raw_text).decode("utf-8")
        except Exception:
            user_text = raw_text

        reply = handle_query(user_text)
        return jsonify({"input": user_text, "reply": reply})

    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ---------------------------------------
# 🧩 Debug: MongoDB 狀態
# ---------------------------------------
@app.route("/debug/mongo")
def debug_mongo():
    try:
        count = col.count_documents({})
        server_info = mongo_client.server_info()
        return jsonify({
            "status": "success",
            "db": MONGO_DB,
            "collection": MONGO_COL,
            "record_count": count,
            "server_info": server_info.get("version", "unknown"),
            "uri": MONGO_URI
        })
    except Exception as e:
        return jsonify({"status": "fail", "error": str(e)}), 500


# ---------------------------------------
# 🚀 主程式入口
# ---------------------------------------
if __name__ == "__main__":
    print("Starting Flask server...")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
