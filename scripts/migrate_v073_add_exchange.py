# scripts/migrate_v073_add_exchange.py
from __future__ import annotations
import sqlite3
import os

# Путь к SQLite-файлу такой же, как в core/db.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # .../scripts
ROOT_DIR = os.path.dirname(BASE_DIR)                  # корень проекта
DB_PATH  = os.path.join(ROOT_DIR, "data", "bot.db")

def has_exchange_column(conn: sqlite3.Connection) -> bool:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(bot_pairs)")
    cols = [row[1] for row in cur.fetchall()]
    return "exchange" in cols

def migrate(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    if not has_exchange_column(conn):
        print("[migrate] adding column bot_pairs.exchange ...")
        cur.execute("ALTER TABLE bot_pairs ADD COLUMN exchange TEXT NOT NULL DEFAULT 'gate'")
    else:
        print("[migrate] column bot_pairs.exchange already exists")

    print("[migrate] backfilling NULL/empty -> 'gate' ...")
    cur.execute("UPDATE bot_pairs SET exchange='gate' WHERE exchange IS NULL OR exchange=''")
    conn.commit()
    print("[migrate] done")

def demo_select(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("SELECT idx, pair, exchange FROM bot_pairs ORDER BY idx LIMIT 10")
    rows = cur.fetchall()
    print("[check] first rows:", rows)

if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"DB file not found: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, isolation_level=None, check_same_thread=False)
    migrate(conn)
    demo_select(conn)
    conn.close()
