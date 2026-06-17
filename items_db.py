import sqlite3
import os
import requests

DB_PATH = os.path.join(os.path.dirname(__file__), "items_cache.db")

def _download_items():
    try:
        resp = requests.get("https://api.opendota.com/api/constants/items", timeout=30)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        items = {}
        for key, info in data.items():
            if key.startswith("item_"):
                item_id = int(key[5:])
                name = info.get("name", key).replace("item_", "").replace("_", " ").title()
                items[item_id] = name
        return items
    except Exception:
        return {}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS items
                   (id INTEGER PRIMARY KEY, name TEXT)""")
    cur.execute("SELECT COUNT(*) FROM items")
    if cur.fetchone()[0] == 0:
        items = _download_items()
        cur.executemany("INSERT OR REPLACE INTO items VALUES (?, ?)", items.items())
        conn.commit()
    conn.close()

def get_item_name(item_id: int) -> str | None:
    if item_id == 0:
        return None
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM items WHERE id = ?", (item_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else f"Item_{item_id}"