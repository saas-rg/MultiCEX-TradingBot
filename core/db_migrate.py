# core/db_migrate.py
from __future__ import annotations
import os
from typing import Any
from core.db import get_conn

def _is_pg(conn: Any) -> bool:
    # Определяем бэкенд по переменной окружения (как в core/db.py)
    return bool(os.getenv("DATABASE_URL", "").strip())

# ---------- SQLite helpers ----------

def _sqlite_has_column(conn: Any, table: str, column: str) -> bool:
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        cols = [row[1] for row in cur.fetchall()]
        return column in cols
    finally:
        try:
            cur and cur.close()
        except Exception:
            pass

def _sqlite_exec(conn: Any, sql: str) -> None:
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(sql)
    finally:
        try:
            cur and cur.close()
        except Exception:
            pass

def _sqlite_add_exchange(conn: Any) -> None:
    # Добавляем колонку, если её нет
    if not _sqlite_has_column(conn, "bot_pairs", "exchange"):
        _sqlite_exec(conn, "ALTER TABLE bot_pairs ADD COLUMN exchange TEXT NOT NULL DEFAULT 'gate'")
    # Бэкфилл
    _sqlite_exec(conn, "UPDATE bot_pairs SET exchange='gate' WHERE exchange IS NULL OR exchange=''")

# ---------- Postgres helpers ----------

def _pg_exec(conn: Any, sql: str) -> None:
    cur = None
    try:
        cur = conn.cursor()
        cur.execute(sql)
    finally:
        try:
            cur and cur.close()
        except Exception:
            pass

def _pg_add_exchange(conn: Any) -> None:
    _pg_exec(conn, """
        ALTER TABLE bot_pairs
            ADD COLUMN IF NOT EXISTS exchange VARCHAR(16) NOT NULL DEFAULT 'gate';
    """)
    _pg_exec(conn, """
        UPDATE bot_pairs SET exchange='gate'
         WHERE exchange IS NULL OR exchange='';
    """)

# ---------- Public API ----------

def migrate_to_v073_add_exchange() -> None:
    """
    v0.7.3: bot_pairs.exchange (+бэкфилл gate). Идемпотентно для SQLite/Postgres.
    """
    conn = get_conn()
    if _is_pg(conn):
        _pg_add_exchange(conn)
    else:
        _sqlite_add_exchange(conn)

def run_all() -> None:
    """ Точка входа для всех миграций этой ветки. """
    migrate_to_v073_add_exchange()
