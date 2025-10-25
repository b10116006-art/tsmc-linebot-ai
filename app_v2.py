# app_v2.py
import os
import re
import csv
import io
import smtplib
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

from flask import Flask, request, abort, jsonify

# === LINE v2/v3 相容處理（你現有 token/secret 用舊 import 也能跑）===
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

from pymongo import MongoClient
from bson.son import SON

# ======== 環境變數（請在 Render → Environment 設定）========
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

MONGO_URI = os.getenv("MONGO_URI")  # 例如：mongodb+srv://<user>:<pass>@cluster0.xxxx.mongodb.net/?retryWrites=true&w=majority
MONGO_DB = os.getenv("MONGO_DB", "tsmc_ai")
MONGO_COL = os.getenv("MONGO_COLLECTION", "lot_scrap_records")

# Gmail 寄信
MAIL_FROM = os.getenv("MAIL_FROM", "b10116006@gmail.com")        # 你的寄件信箱
MAIL_TO = os.getenv("MAIL_TO", "b10116006@gmail.com")            # 收件信箱（可逗號分隔）
MAIL_USER = os.getenv("MAIL_USER", "b10116006@gmail.com")        # Gmail 帳號
MAIL_PASS = os.getenv("MAIL_PASS")                               # Gmail 應用程式密碼（請用 App Password）
MAIL_SUBJECT_PREFIX = os.getenv("MAIL_SUBJECT_PREFIX", "[TSMC LineBot AI]")

# 通知門檻
ALERT_LOOKBACK_DAYS = int(os.getenv("ALERT_LOOKBACK_DAYS", "7"))
ALERT_SCRAP_QTY = int(os.getenv("ALERT_SCRAP_QTY", "10"))

# ======== 初始化 ========
app = Flask(__name__)

if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_CHANNEL_SECRET:
    raise RuntimeError("LINE token/secret 尚未在環境變數設定")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# Mongo 連線
if not MONGO_URI:
    raise RuntimeError("MONGO_URI 尚未設定")

mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB]
col = db[MONGO_COL]

# ======== 工具 ========

def excel_serial_to_datetime(x):
    """支援 Excel serial（如 44611）或 ISO 字串 / datetime"""
    try:
        if isinstance(x, (int, float)):  # Excel 日期序號（以 1899-12-30 為 Day 0）
            base = datetime(1899, 12, 30)
            return base + timedelta(days=float(x))
        if isinstance(x, str):
            # 盡量 parse ISO-like 字串
            return datetime.fromisoformat(x.replace("Z", "+00:00")).replace(tzinfo=None)
        if isinstance(x, datetime):
            return x
    except Exception:
        pass
    return None

def norm_text(s: str) -> str:
    return (s or "").strip().lower()

def send_email(subject: str, html: str):
    """用 Gmail SMTP 寄信（需 MAIL_USER / MAIL_PASS）"""
    if not MAIL_PASS:
        app.logger.warning("未設定 MAIL_PASS（Gmail App Password），略過寄信")
        return

    msg = MIMEMultipart("alternative")
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO
    msg["Subject"] = f"{MAIL_SUBJECT_PREFIX} {subject}"

    part = MIMEText(html, "html", "utf-8")
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(MAIL_USER, MAIL_PASS)
        smtp.sendmail(MAIL_FROM, MAIL_TO.split(","), msg.as_string())

def agg_top(field: str, date_from: datetime, date_to: datetime, limit=3):
    """聚合：指定欄位（layer/defect/product）在日期區間的 Top N"""
    match = {"Date_dt": {"$gte": date_from, "$lt": date_to}}
    pipeline = [
        {"$match": match},
        {"$group": {"_id": f"${field}", "scrap": {"$sum": {"$ifNull": ["$ScrapQty", 1]}}}},
        {"$sort": SON([("scrap", -1)])},
        {"$limit": limit},
    ]
    res = list(col.aggregate(pipeline))
    return [{"name": (r["_id"] or "N/A"), "qty": int(r["scrap"])} for r in res]

def recent_alerts_html(days: int, threshold: int) -> str:
    """7 天內報廢量超過門檻的機台/產品列表（你可依實際欄位調整 group key）"""
    since = datetime.utcnow() - timedelta(days=days)
    pipeline = [
        {"$match": {"Date_dt": {"$gte": since}}},
        {"$group": {
            "_id": {"Product": "$Product", "Layer": "$Layer", "Defect": "$Defect Type"},
            "scrap": {"$sum": {"$ifNull": ["$ScrapQty", 1]}}
        }},
        {"$match": {"scrap": {"$gte": threshold}}},
        {"$sort": SON([("scrap", -1)])}
    ]
    rows = list(col.aggregate(pipeline))
    if not rows:
        return "<p>過去 7 天沒有達到門檻的報廢項目。</p>"

    buf = ["<table border=1 cellspacing=0 cellpadding=6>",
           "<tr><th>Product</th><th>Layer</th><th>Defect Type</th><th>Scrap</th></tr>"]
    for r in rows:
        gid = r["_id"] or {}
        buf.append(f"<tr><td>{gid.get('Product','')}</td>"
                   f"<td>{gid.get('Layer','')}</td>"
                   f"<td>{gid.get('Defect','')}</td>"
                   f"<td>{int(r['scrap'])}</td></tr>")
    buf.append("</table>")
    return "\n".join(buf)

def ensure_date_index():
    """把來源資料的 Date 欄位轉為 Date_dt 供查詢排序"""
    for doc in col.find({"Date_dt": {"$exists": False}}, {"Date": 1}):
        dt = excel_serial_to_datetime(doc.get("Date"))
        if dt:
            col.update_one({"_id": doc["_id"]}, {"$set": {"Date_dt": dt}})
    col.create_index("Date_dt")

# 一進程就準備好 Date_dt
ensure_date_index()

# ======== LINE Webhook ========

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("Invalid signature")
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def on_message(event: MessageEvent):
    text = norm_text(event.message.text)

    try:
        reply = dispatch_query(text)
        if not reply:
            reply = default_help()
    except Exception as e:
        app.logger.error("dispatch error: %s\n%s", e, traceback.format_exc())
        reply = "系統忙線中，請稍後再試 🙏"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))

def default_help():
    return (
        "Hi，我能協助查詢報廢概況（中英都可）：\n"
        "• 本月 layer 前三名 / 本年 layer top 3\n"
        "• 本月 defect type 前三名 / product 前三名\n"
        "• 問：M3 為什麼報廢？負責人是誰？（支援模糊）\n"
        "範例：\n"
        "  - 本月 layer 前三名\n"
        "  - top3 defect this month\n"
        "  - metal3 為啥報廢\n"
    )

def dispatch_query(text: str) -> str:
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    # 1) Top3 問句（layer / defect / product）
    is_month = any(k in text for k in ["本月", "this month", "month"])
    is_year  = any(k in text for k in ["今年", "本年", "this year", "year"])

    def _reply_top(kind: str, start_dt: datetime):
        field_map = {"layer": "Layer", "defect": "Defect Type", "product": "Product"}
        items = agg_top(field_map[kind], start_dt, now, limit=3)
        if not items:
            return f"{ '本月' if start_dt==month_start else '本年'} {kind} 沒有資料"
        lines = [f"{'本月' if start_dt==month_start else '本年'} {kind} Top 3:"]
        for i, r in enumerate(items, 1):
            lines.append(f"{i}. {r['name']}：{r['qty']}")
        return "\n".join(lines)

    if any(k in text for k in ["layer", "層", "層別"]):
        if is_month: return _reply_top("layer", month_start)
        if is_year:  return _reply_top("layer", year_start)
    if any(k in text for k in ["defect", "defect type", "缺陷", "不良"]):
        if is_month: return _reply_top("defect", month_start)
        if is_year:  return _reply_top("defect", year_start)
    if any(k in text for k in ["product", "產品"]):
        if is_month: return _reply_top("product", month_start)
        if is_year:  return _reply_top("product", year_start)

    # 2) 問：M3/metal3 為什麼報廢？負責人是誰？
    m = re.search(r"(m\s*?\d+|metal\s*\d+|metal\d+|m\d+|[a-z]*\d+)", text, re.I)
    if m:
        key = m.group(0).replace(" ", "").lower()
        # 近 90 天
        since = now - timedelta(days=90)
        q = {"Date_dt": {"$gte": since}, "Layer": {"$regex": key, "$options": "i"}}
        doc = col.find_one(q, sort=[("Date_dt", -1)])
        if not doc:
            return f"找不到 {key.upper()} 近期的報廢紀錄"
        # 欄位對應：Owner、Previous/Next Process 可能在檔案第二表，若無則顯示 N/A
        reason = doc.get("Defect Type") or doc.get("Dominant Defect") or "N/A"
        owner  = doc.get("Owner") or "N/A"
        prev_p = doc.get("Previous Process") or doc.get("previous_process") or "N/A"
        next_p = doc.get("Next Process") or doc.get("next_process") or "N/A"
        prod   = doc.get("Product") or "N/A"
        scrap  = int(doc.get("ScrapQty", 1))
        dt_str = doc.get("Date_dt").strftime("%Y-%m-%d") if doc.get("Date_dt") else "N/A"
        return (f"{key.upper()} 近期報廢\n"
                f"• 日期：{dt_str}\n"
                f"• 產品：{prod}\n"
                f"• 原因：{reason}\n"
                f"• 負責人：{owner}\n"
                f"• 前製程：{prev_p}\n"
                f"• 後製程：{next_p}\n"
                f"• Scrap：{scrap}")

    return ""  # 讓 caller 回傳 help

# ======== 每日自動通知（Render dyno 會常駐即可）========
from threading import Thread
import time

def daily_notifier():
    """每天 09:00 UTC 寄出 7 天內達門檻的清單（Render 若會睡，可改用外部 cron）"""
    while True:
        try:
            now = datetime.utcnow()
            # 等待到整點 09:00 UTC
            target = now.replace(hour=9, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            time.sleep((target - now).total_seconds())

            html = f"""
            <h3>過去 {ALERT_LOOKBACK_DAYS} 天報廢告警（≧ {ALERT_SCRAP_QTY}）</h3>
            {recent_alerts_html(ALERT_LOOKBACK_DAYS, ALERT_SCRAP_QTY)}
            """
            send_email("Daily Scrap Alert", html)
        except Exception as e:
            # 不中斷循環
            print("Notifier error:", e, traceback.format_exc())
            time.sleep(300)

# 背景執行（如果你用 Render free web，有時會休眠；正式環境建議獨立 worker）
Thread(target=daily_notifier, daemon=True).start()

# ======== 管理/工具 API（可選）========
@app.route("/healthz")
def healthz():
    return jsonify(ok=True, time=datetime.utcnow().isoformat())

@app.route("/admin/upload_csv", methods=["POST"])
def upload_csv():
    """
    上傳 CSV 匯入（欄位可包含：
    Lot ID, Site, Product, Step, Layer, Defect Type, Wafer Qty, Adj Wafer Qty, Date(Excel)
    以及：Predicted Scrap Load, Dominant Defect, Risk Level, Suggested Action ...）
    """
    if "file" not in request.files:
        return "file 欄位未上傳", 400
    f = request.files["file"]
    content = f.read().decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(content))
    cnt = 0
    for row in reader:
        # 欄位對齊
        doc = {
            "Lot ID": row.get("Lot ID"),
            "Site": row.get("Site"),
            "Product": row.get("Product"),
            "Step": row.get("Step"),
            "Layer": row.get("Layer"),
            "Defect Type": row.get("Defect Type") or row.get("Dominant Defect"),
            "Wafer Qty": try_int(row.get("Wafer Qty")),
            "Adj Wafer Qty": try_int(row.get("Adj Wafer Qty")),
            "ScrapQty": try_int(row.get("ScrapQty")) or try_int(row.get("Wafer Qty")) or 1,
            "Predicted Scrap Load": try_float(row.get("Predicted Scrap Load")),
            "Dominant Defect": row.get("Dominant Defect"),
            "Risk Level": row.get("Risk Level"),
            "Suggested Action": row.get("Suggested Action"),
        }
        # 日期
        date_raw = row.get("Date")
        dt = excel_serial_to_datetime(try_float(date_raw) if date_raw else None)
        if not dt and date_raw:
            # 再試字串
            dt = excel_serial_to_datetime(date_raw)
        if dt:
            doc["Date_dt"] = dt
            doc["Date"] = date_raw
        col.insert_one(doc)
        cnt += 1
    return jsonify(inserted=cnt)

def try_int(x):
    try:
        return int(float(x))
    except Exception:
        return None

def try_float(x):
    try:
        return float(x)
    except Exception:
        return None

# ======== 啟動 ========
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
