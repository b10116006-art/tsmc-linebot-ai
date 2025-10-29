# app_v2_secure.py
from flask import Flask, request, jsonify, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from pymongo import MongoClient
from datetime import datetime, timedelta
from rapidfuzz import fuzz
import pandas as pd
import base64
import os
import json
import jieba

# ========== 基本設定 ==========
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Flask server is running successfully on Render."

# Load environment variables
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://b10116006_db_user:MyMongo123@cluster0.x1lapiv.mongodb.net/tsmc_ai")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = MongoClient(MONGO_URI)
db = client["tsmc_ai"]
collection = db["lot_scrap_records"]

# ========== Line Bot Webhook ==========
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK', 200


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_text = event.message.text.strip()
    reply_msg = handle_query(user_text)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg))


# ========== 查詢邏輯 ==========
def handle_query(query_text):
    """主查詢邏輯，支援模糊比對 + 最近90天"""
    try:
        # Base64 解碼
        try:
            decoded = base64.b64decode(query_text).decode("utf-8")
            text = decoded
        except Exception:
            text = query_text

        text = text.strip()
        print(f"🔍 使用者查詢: {text}")

        # ---- 模糊查 Layer ----
        layers = [d["Layer"] for d in collection.find({}, {"Layer": 1}) if "Layer" in d]
        best_match = max(layers, key=lambda l: fuzz.partial_ratio(l, text)) if layers else None

        # ---- 時間篩選 (近90天) ----
        start_date = datetime.now() - timedelta(days=90)
        query = {"Date_dt": {"$gte": start_date}}
        if best_match:
            query["Layer"] = best_match

        docs = list(collection.find(query))
        if not docs:
            return f"找不到 {text} 相關的報廢紀錄。"

        # ---- 統計分析 ----
        df = pd.DataFrame(docs)
        if "ScrapQty" in df.columns:
            total_scrap = int(df["ScrapQty"].sum())
        else:
            total_scrap = len(df)

        if "DefectType" in df.columns:
            top_defects = (
                df["DefectType"].value_counts().head(3).to_dict()
                if len(df["DefectType"]) else {}
            )
            defect_str = "\n".join([f"{k}: {v}" for k, v in top_defects.items()])
        else:
            defect_str = "(無 DefectType 欄位)"

        return f"📊 {text} 最近90天統計：\n報廢筆數：{len(df)} 筆\n報廢總量：{total_scrap}\n主要缺陷：\n{defect_str}"

    except Exception as e:
        return f"❌ 查詢發生錯誤：{str(e)}"


# ========== Debug 介面 ==========
@app.route("/debug/mongo")
def debug_mongo():
    key = request.args.get("key", "")
    if key != "tsmc123":
        abort(403)
    count = collection.count_documents({})
    return jsonify({
        "status": "success",
        "db": "tsmc_ai",
        "collection": "lot_scrap_records",
        "record_count": count,
        "server_info": client.server_info().get("version", "unknown"),
        "uri": MONGO_URI
    })


@app.route("/debug/query", methods=["POST"])
def debug_query():
    key = request.args.get("key", "")
    if key != "tsmc123":
        abort(403)
    try:
        raw = request.get_data()
        data = json.loads(raw.decode("utf-8"))
        text = data.get("text", "")
        reply = handle_query(text)
        return jsonify({"input": text, "reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ========== 啟動 ==========
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
