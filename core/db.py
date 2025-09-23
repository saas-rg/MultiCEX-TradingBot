# core/db.py
"""
DB helper с fallback'ом:
- Если DATABASE_URL задан -> используем Postgres (psycopg2).
- Иначе -> используем локальную SQLite базу в ./data/bot.db.
Оборачиваем метод connection.cursor, чтобы он возвращал объект, поддерживающий
контекстный менеджер (with ... as cur:).
"""

import os
import threading

_DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_USE_PG = bool(_DATABASE_URL)

# Путь к sqlite файлу (в папке data/ рядом с пакетом core/)
_SQLITE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "bot.db")
try:
    os.makedirs(os.path.dirname(_SQLITE_PATH), exist_ok=True)
except Exception:
    pass

_pg_conn = None
_sqlite_conn = None
_lock = threading.Lock()


def _wrap_sqlite_cursor(conn):
    """
    Заменяет conn.cursor на функцию, возвращающую CursorWrapper,
    который поддерживает with-менеджер.
    """
    if getattr(conn, "_wrapped_cursor", False):
        return

    class CursorWrapper:
        def __init__(self, raw_cursor):
            self._cur = raw_cursor

        def __enter__(self):
            return self._cur

        def __exit__(self, exc_type, exc, tb):
            try:
                self._cur.close()
            except Exception:
                pass
            # do not suppress exceptions
            return False

        def __getattr__(self, item):
            return getattr(self._cur, item)

    orig_cursor = conn.cursor

    def cursor_with_wrapper(*args, **kwargs):
        raw = orig_cursor(*args, **kwargs)
        return CursorWrapper(raw)

    try:
        conn.cursor = cursor_with_wrapper
        conn._wrapped_cursor = True
    except Exception:
        # если не получилось — молча продолжаем, ошибка будет проявляться при использовании
        pass


def get_conn():
    """
    Возвращает соединение:
     - Postgres (psycopg2) если DATABASE_URL задан
     - Иначе sqlite3 (файл ./data/bot.db)
    """
    global _pg_conn, _sqlite_conn, _USE_PG

    if _USE_PG:
        if _pg_conn and not getattr(_pg_conn, "closed", False):
            return _pg_conn
        try:
            import psycopg2
        except Exception as e:
            raise RuntimeError("psycopg2 required for DATABASE_URL usage but not installed: " + str(e))
        conn = psycopg2.connect(_DATABASE_URL, sslmode="require")
        conn.autocommit = True
        _pg_conn = conn
        return _pg_conn

    # sqlite path
    if _sqlite_conn:
        # убедимся, что wrapper установлен
        try:
            _wrap_sqlite_cursor(_sqlite_conn)
        except Exception:
            pass
        return _sqlite_conn

    import sqlite3
    # isolation_level=None -> autocommit mode
    conn = sqlite3.connect(_SQLITE_PATH, isolation_level=None, check_same_thread=False)
    # row_factory оставляем дефолтным; важно — conn.cursor() вернётся обёрнутый
    _sqlite_conn = conn
    # применяем обёртку сразу
    _wrap_sqlite_cursor(_sqlite_conn)
    return _sqlite_conn


def init_db():
    """
    Создаёт таблицы для обеих реализаций (Postgres/SQLite).
    """
    conn = get_conn()

    if _USE_PG:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
              key text PRIMARY KEY,
              value text NOT NULL,
              updated_at timestamptz DEFAULT now()
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_runtime (
              key text PRIMARY KEY,
              value text NOT NULL,
              updated_at timestamptz DEFAULT now()
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_pairs (
              idx smallint PRIMARY KEY,
              pair text NOT NULL,
              deviation_pct numeric(36,18) NOT NULL,
              quote numeric(36,12) NOT NULL,
              lot_size_base numeric(36,18) NOT NULL,
              gap_mode text NOT NULL,
              gap_switch_pct numeric(36,18) NOT NULL,
              enabled boolean NOT NULL DEFAULT true,
              updated_at timestamptz DEFAULT now()
            );
            """)
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_bot_pairs_pair ON bot_pairs(pair);")
        return

    # SQLite DDL (совместимый)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bot_settings (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bot_runtime (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bot_pairs (
      idx INTEGER PRIMARY KEY,
      pair TEXT NOT NULL,
      deviation_pct NUMERIC NOT NULL,
      quote NUMERIC NOT NULL,
      lot_size_base NUMERIC NOT NULL,
      gap_mode TEXT NOT NULL,
      gap_switch_pct NUMERIC NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)
    try:
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_bot_pairs_pair ON bot_pairs(pair);")
    except Exception:
        pass
    # Не закрываем курсор явно — wrapper закроет его при выходе из контекста, но тут мы не в with
    try:
        cur.close()
    except Exception:
        pass
