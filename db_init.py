import random
import string
from datetime import datetime, timedelta
from pymongo import MongoClient, ASCENDING, DESCENDING
import os

MONGO_URI = os.getenv("MONGO_URI", "mongodb://root:mongo123@mongo:27017/")
DB_NAME = os.getenv("MONGO_DB", "tsmc_linebot")

# 基本字典
PRODUCTS = ["n22", "n28", "SHR"]
LAYERS = ["STI", "PSG", "ILD", "M1", "M2", "M3", "M4", "M5", "V1", "CA"]
DEFECT_TYPES = [
    "Metal Residue", "Organic Residue", "Particle", "Diamond Scratch",
    "Micro-scratches", "Dishing", "Water Marks", "Corrosion"
]
OWNERS = {
    "STI": "Kevin Chen",
    "PSG": "Eric Wu",
    "ILD": "Jason Lin",
    "M1": "Kelly Lin",
    "M2": "David Wang",
    "M3": "Poyun Chen",
    "M4": "Emily Wang",
    "M5": "Alan Tsai",
    "V1": "Alice Huang",
    "CA": "Mark Liu"
}
PREV_NEXT = {
    "STI": ("CMP", "PSG"),
    "PSG": ("STI", "ILD"),
    "ILD": ("PSG", "Lithography"),
    "M1": ("Lithography", "Etch"),
    "M2": ("Lithography", "Etch"),
    "M3": ("Lithography", "Etch"),
    "M4": ("Lithography", "Etch"),
    "M5": ("Lithography", "Etch"),
    "V1": ("Etch", "Deposition"),
    "CA": ("Etch", "Metal")
}
RISK = ["Low", "Medium", "High"]

REASONS = {
    "Metal Residue": "Metal film residual causing shorts",
    "Organic Residue": "Resist residual not fully stripped",
    "Particle": "Particle contamination from FOUP/equipment",
    "Diamond Scratch": "Diamond polish induced scratches",
    "Micro-scratches": "Minor surface scratch after handling",
    "Dishing": "CMP over-polish inducing recess",
    "Water Marks": "Rinse/dry marks not fully removed",
    "Corrosion": "Chemical corrosion / oxidation"
}

def random_lot():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def main():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    col = db["lot_scrap_records"]
    logs = db["alert_logs"]

    col.drop()
    logs.drop()

    docs = []
    now = datetime.utcnow()
    start = now - timedelta(days=365*3)  # 三年內
    for _ in range(1000):
        layer = random.choice(LAYERS)
        product = random.choice(PRODUCTS)
        defect = random.choice(DEFECT_TYPES)
        ts = start + timedelta(seconds=random.randint(0, int((now-start).total_seconds())))
        owner = OWNERS.get(layer, "Unknown Owner")
        prev_p, next_p = PREV_NEXT.get(layer, ("NA", "NA"))
        reason = REASONS[defect]
        docs.append({
            "lot": random_lot(),
            "product": product,
            "layer": layer,
            "defect_type": defect,
            "reason": reason,
            "owner": owner,
            "previous_process": prev_p,
            "next_process": next_p,
            "root_cause": reason,
            "risk_level": random.choice(RISK),
            "timestamp": ts
        })
    col.insert_many(docs)

    # 索引
    col.create_index([("layer", ASCENDING)])
    col.create_index([("defect_type", ASCENDING)])
    col.create_index([("product", ASCENDING)])
    col.create_index([("timestamp", DESCENDING)])
    col.create_index([("owner", ASCENDING)])

    print("✅ Mongo seeded with 1000 documents into 'lot_scrap_records'.")

if __name__ == "__main__":
    main()
