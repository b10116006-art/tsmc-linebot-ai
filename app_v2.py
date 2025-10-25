import os
import csv
import traceback
from flask import Flask, request, jsonify
from pymongo import MongoClient, errors

# 初始化 Flask
app = Flask(__name__)

# 取得環境變數
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "tsmc_ai")
MONGO_COL = os.getenv("MONGO_COLLECTION", "lot_scrap_records")

# ======== MongoDB 連線偵錯區 ========
@app.route("/debug/mongo", methods=["GET"])
def debug_mongo():
    result = {"uri": MONGO_URI, "db": MONGO_DB, "collection": MONGO_COL}
    try:
        if not MONGO_URI:
            raise ValueError("MONGO_URI 未設定")

        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        info = client.server_info()  # 測試實際連線
        db = client[MONGO_DB]
        col = db[MONGO_COL]
        count = col.estimated_document_count()

        result.update({
            "status": "success",
            "server_info": info.get("version", "unknown"),
            "record_count": count
        })
    except errors.ServerSelectionTimeoutError as e:
        result.update({
            "status": "timeout",
            "error": str(e)
        })
    except Exception as e:
        result.update({
            "status": "error",
            "error": traceback.format_exc()
        })
    return jsonify(result)
# ====================================


@app.route("/admin/upload_csv", methods=["POST"])
def upload_csv():
    try:
        if "file" not in request.files:
            return jsonify({"error": "未上傳檔案"}), 400

        file = request.files["file"]
        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB]
        col = db[MONGO_COL]

        reader = csv.DictReader(file.stream.read().decode("utf-8-sig").splitlines())
        data = list(reader)
        if not data:
            return jsonify({"error": "CSV 為空"}), 400

        # 批次寫入
        col.insert_many(data)
        return jsonify({"inserted": len(data)})
    except Exception as e:
        return jsonify({
            "error": str(e),
            "trace": traceback.format_exc()
        }), 500


@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "message": "TSMC Linebot AI Service running"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
