import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from pymongo import MongoClient
import os

CONFIG_PATH = os.getenv("ALERT_CONFIG", "alert_config.json")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:mongo123@localhost:27017/")
DB_NAME = os.getenv("MONGO_DB", "tsmc_linebot")

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def send_mail(cfg, to_name, layer, product, count):
    subject = f"[TSMC Alert] {layer} ({product}) Scrap Spike"
    body = f"""Hi {to_name},

We detected an abnormal scrap increase in the last {cfg['period_days']} days.
Layer/Product: {layer} / {product}
Count: {count}

Please check related process/equipment ASAP.

— {cfg.get('brand_watermark', 'TSMC AI AutoPipeline System')}
"""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = cfg["smtp_user"]
    # 若沒有真實信箱，可暫時寄到自己（示範）
    msg["To"] = cfg["smtp_user"]

    with smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"]) as s:
        s.starttls()
        s.login(cfg["smtp_user"], cfg["smtp_pass"])
        s.send_message(msg)

def main():
    cfg = load_config()
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    col = db["lot_scrap_records"]
    logs = db["alert_logs"]

    since = datetime.utcnow() - timedelta(days=cfg["period_days"])
    pipeline = [
        {"$match": {"timestamp": {"$gte": since}}},
        {"$group": {"_id": {"layer": "$layer", "product": "$product", "owner": "$owner"},
                    "count": {"$sum": 1}}},
        {"$match": {"count": {"$gte": cfg["threshold"]}}}
    ]
    hits = list(col.aggregate(pipeline))

    for h in hits:
        layer = h["_id"]["layer"]
        product = h["_id"]["product"]
        owner = h["_id"]["owner"]
        count = h["count"]

        # 檢查是否已寄送過（避免狂寄）
        dup = logs.find_one({
            "layer": layer, "product": product,
            "since": {"$gte": since.date().isoformat()}
        })
        if dup:
            continue

        # send mail
        try:
            send_mail(cfg, owner, layer, product, count)
            logs.insert_one({
                "layer": layer,
                "product": product,
                "owner": owner,
                "count": count,
                "since": since.date().isoformat(),
                "sent_at": datetime.utcnow()
            })
            print(f"📧 Alert sent: {layer}/{product} ({count}) -> {owner}")
        except Exception as e:
            print("❌ Mail error:", e)

if __name__ == "__main__":
    main()
