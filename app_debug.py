from flask import Flask, request
import csv, io, traceback

app = Flask(__name__)

@app.route("/admin/upload_csv", methods=["POST"])
def upload_csv_debug():
    try:
        if "file" not in request.files:
            return "file 欄位未上傳", 400

        f = request.files["file"]
        content = f.read().decode("utf-8", errors="ignore")
        reader = csv.DictReader(io.StringIO(content))

        rows = list(reader)
        return {
            "status": "ok",
            "row_count": len(rows),
            "columns": reader.fieldnames
        }

    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "trace": traceback.format_exc().splitlines()
        }, 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
