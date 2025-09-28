# core/params.py
from decimal import Decimal
from typing import Dict, Any, List, TypedDict, Literal, Tuple
from config import (
    PAIR, DEVIATION_PCT, QUOTE_USDT, LOT_SIZE_BASE, GAP_MODE, GAP_SWITCH_PCT,
)
from .db import get_conn, init_db
from core.exchange_proxy import available_exchanges

GapMode = Literal["off", "down_only", "symmetric"]

class PairCfg(TypedDict, total=False):
    idx: int
    pair: str
    deviation_pct: Decimal
    quote: Decimal
    lot_size_base: Decimal
    gap_mode: GapMode
    gap_switch_pct: Decimal
    enabled: bool
    # начиная с v0.7.2/0.7.3 — поле биржи (в 0.7.3 уже есть в БД; в старых БД — дефолт 'gate')
    exchange: str  # "gate"

ALLOWED_KEYS = {
    "PAIR": str,
    "DEVIATION_PCT": Decimal,
    "QUOTE": Decimal,
    "LOT_SIZE_BASE": Decimal,
    "GAP_MODE": str,
    "GAP_SWITCH_PCT": Decimal,
    # NEW: режим телеметрии
    "REPORT_INTERVAL": str,  # "hourly" | "30m" | "15m" | "5m"
}

def _coerce(k: str, v: str):
    t = ALLOWED_KEYS.get(k)
    if t is Decimal:
        return Decimal(v)
    if t is str:
        return str(v)
    return v

# --- helper: detect sqlite connection ---
def _is_sqlite_conn(conn) -> bool:
    try:
        mod = conn.__class__.__module__
        if mod and mod.startswith("sqlite3"):
            return True
    except Exception:
        pass
    if not hasattr(conn, "closed") and hasattr(conn, "execute"):
        return True
    return False

def _has_column(conn, table: str, column: str) -> bool:
    """
    Идемпотентно проверяет наличие колонки в таблице для SQLite/Postgres.
    """
    cur = None
    try:
        cur = conn.cursor()
        if _is_sqlite_conn(conn):
            cur.execute(f"PRAGMA table_info({table})")
            rows = cur.fetchall() or []
            cols = [row[1] for row in rows]  # имя колонки — 2-й столбец
            return column in cols
        else:
            cur.execute("""
                SELECT 1
                  FROM information_schema.columns
                 WHERE table_name=%s AND column_name=%s
                 LIMIT 1
            """, (table, column))
            return cur.fetchone() is not None
    except Exception:
        return False
    finally:
        try:
            cur and cur.close()
        except Exception:
            pass

def ensure_schema():
    """
    Инициализирует БД и при пустой таблице bot_pairs создаёт дефолтную запись.
    Работает как с SQLite, так и с Postgres (Heroku).
    """
    init_db()
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        # Если таблицы нет — init_db() должен был её создать; здесь просто проверим содержимое
        cur.execute("SELECT count(*) FROM bot_pairs;")
        row = cur.fetchone()
        cnt = int(row[0]) if row else 0

        has_ex = _has_column(conn, "bot_pairs", "exchange")

        if cnt == 0:
            if _is_sqlite_conn(conn):
                if has_ex:
                    cur.execute(
                        "INSERT INTO bot_pairs(idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled, exchange) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (1, PAIR, str(DEVIATION_PCT), str(QUOTE_USDT), str(LOT_SIZE_BASE), GAP_MODE, str(GAP_SWITCH_PCT), 1, "gate")
                    )
                else:
                    cur.execute(
                        "INSERT INTO bot_pairs(idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (1, PAIR, str(DEVIATION_PCT), str(QUOTE_USDT), str(LOT_SIZE_BASE), GAP_MODE, str(GAP_SWITCH_PCT), 1)
                    )
            else:
                # Postgres: используем %s и булевы типы True/False
                if has_ex:
                    cur.execute(
                        "INSERT INTO bot_pairs(idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled, exchange) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (1, PAIR, str(DEVIATION_PCT), str(QUOTE_USDT), str(LOT_SIZE_BASE),
                         GAP_MODE, str(GAP_SWITCH_PCT), True, "gate")
                    )
                else:
                    cur.execute(
                        "INSERT INTO bot_pairs(idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (1, PAIR, str(DEVIATION_PCT), str(QUOTE_USDT), str(LOT_SIZE_BASE),
                         GAP_MODE, str(GAP_SWITCH_PCT), True)
                    )
    finally:
        try:
            if cur is not None:
                cur.close()
        except Exception:
            pass


def get_paused() -> bool:
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM bot_runtime WHERE key='paused';")
        row = cur.fetchone()
        if not row: return False
        val = row[0] if isinstance(row, (list, tuple)) else row
        return str(val).lower() in ("1","true","yes","y")
    finally:
        try:
            if cur is not None: cur.close()
        except Exception: pass

def set_paused(flag: bool):
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        if _is_sqlite_conn(conn):
            cur.execute("INSERT OR REPLACE INTO bot_runtime(key, value, updated_at) VALUES ('paused', ?, CURRENT_TIMESTAMP)",
                        ("true" if flag else "false",))
        else:
            cur.execute("INSERT INTO bot_runtime(key, value) VALUES ('paused', %s) "
                        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
                        ("true" if flag else "false",))
    finally:
        try:
            if cur is not None: cur.close()
        except Exception: pass

# ------- STOP/SHUTDOWN флаг -------
def get_shutdown() -> bool:
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM bot_runtime WHERE key='shutdown';")
        row = cur.fetchone()
        if not row: return False
        val = row[0] if isinstance(row, (list, tuple)) else row
        return str(val).strip().lower() in ("1","true","yes","y","on")
    finally:
        try:
            cur and cur.close()
        except Exception:
            pass

def set_shutdown(flag: bool):
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        if _is_sqlite_conn(conn):
            cur.execute("INSERT OR REPLACE INTO bot_runtime(key, value, updated_at) VALUES ('shutdown', ?, CURRENT_TIMESTAMP)",
                        ("true" if flag else "false",))
        else:
            cur.execute("INSERT INTO bot_runtime(key, value) VALUES ('shutdown', %s) "
                        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
                        ("true" if flag else "false",))
    finally:
        try:
            cur and cur.close()
        except Exception:
            pass
# -------------------------------

def load_overrides() -> Dict[str, Any]:
    conn = get_conn()
    out: Dict[str, Any] = {
        "PAIR": PAIR,
        "DEVIATION_PCT": DEVIATION_PCT,
        "QUOTE": QUOTE_USDT,
        "LOT_SIZE_BASE": LOT_SIZE_BASE,
        "GAP_MODE": GAP_MODE,
        "GAP_SWITCH_PCT": GAP_SWITCH_PCT,
        # дефолт, если не задано в БД
        "REPORT_INTERVAL": "hourly",
    }
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT key, value FROM bot_settings;")
        rows = cur.fetchall()
        for r in rows:
            k = r[0]; v = r[1]
            if k in ALLOWED_KEYS:
                try:
                    out[k] = _coerce(k, v)
                except Exception:
                    pass
    finally:
        try:
            if cur is not None: cur.close()
        except Exception: pass
    return out

def upsert_params(upd: Dict[str, Any]) -> Dict[str, Any]:
    if not upd:
        return load_overrides()
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        is_sqlite = _is_sqlite_conn(conn)
        for k, raw in upd.items():
            if k not in ALLOWED_KEYS: continue
            v = str(_coerce(k, str(raw)))
            if is_sqlite:
                cur.execute("INSERT OR REPLACE INTO bot_settings(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (k, v))
            else:
                cur.execute("INSERT INTO bot_settings(key, value) VALUES (%s, %s) "
                            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (k, v))
    finally:
        try:
            if cur is not None: cur.close()
        except Exception: pass
    return load_overrides()

def _select_pairs_rows(conn, include_disabled: bool, has_exchange: bool) -> Tuple[List[tuple], List[str]]:
    """
    Унифицированный SELECT по bot_pairs с/без колонки exchange.
    Возвращает (rows, cols).
    """
    cur = None
    try:
        cur = conn.cursor()
        base_cols = "idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled"
        cols = base_cols + (", exchange" if has_exchange else "")
        if include_disabled:
            if _is_sqlite_conn(conn):
                cur.execute(f"SELECT {cols} FROM bot_pairs ORDER BY idx ASC")
            else:
                cur.execute(f"SELECT {cols} FROM bot_pairs ORDER BY idx ASC")
        else:
            if _is_sqlite_conn(conn):
                cur.execute(f"SELECT {cols} FROM bot_pairs WHERE enabled = 1 ORDER BY idx ASC")
            else:
                cur.execute(f"SELECT {cols} FROM bot_pairs WHERE enabled = %s ORDER BY idx ASC", (True,))
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description] if getattr(cur, "description", None) else cols.split(", ")
        return rows, colnames
    finally:
        try:
            cur and cur.close()
        except Exception:
            pass

def _as_int(val, default: int) -> int:
    try:
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return int(val)
        s = str(val).strip()
        if not s:
            return default
        return int(float(s))
    except Exception:
        return default

def list_pairs(include_disabled: bool = False) -> List[PairCfg]:
    """
    Возвращает пары из БД. Терпимо относится к NULL/'' в idx, корректно приводит enabled,
    и подставляет 'gate' как биржу по умолчанию для старых БД.
    """
    conn = get_conn()
    has_ex = _has_column(conn, "bot_pairs", "exchange")
    rows, cols = _select_pairs_rows(conn, include_disabled, has_ex)

    out: List[PairCfg] = []
    col_idx = {name: i for i, name in enumerate(cols)}

    for pos, r in enumerate(rows, start=1):
        idx_val = _as_int(r[col_idx["idx"]], pos)

        en_raw = r[col_idx["enabled"]]
        if isinstance(en_raw, bool):
            enabled = en_raw
        elif isinstance(en_raw, (int, float)):
            enabled = bool(int(en_raw))
        else:
            enabled = str(en_raw).strip().lower() in ("1", "true", "yes", "y", "on")

        cfg: PairCfg = PairCfg(
            idx=idx_val,
            pair=str(r[col_idx["pair"]]),
            deviation_pct=Decimal(str(r[col_idx["deviation_pct"]])),
            quote=Decimal(str(r[col_idx["quote"]])),
            lot_size_base=Decimal(str(r[col_idx["lot_size_base"]])),
            gap_mode=str(r[col_idx["gap_mode"]]),
            gap_switch_pct=Decimal(str(r[col_idx["gap_switch_pct"]])),
            enabled=enabled,
        )
        if has_ex and "exchange" in col_idx:
            cfg["exchange"] = (str(r[col_idx["exchange"]]) or "gate").strip().lower()
        else:
            cfg["exchange"] = "gate"
        out.append(cfg)

    return out

def upsert_pairs(pairs: List[PairCfg]) -> List[PairCfg]:
    """
    Принимаем пары из /admin и полностью перезаписываем таблицу bot_pairs.
    Теперь сохраняем exchange из запроса (валидация по реестру), без жёсткого 'gate'.
    """
    allowed_ex = set(available_exchanges())

    norm: List[PairCfg] = []
    seen_pairs: set[Tuple[str,str]] = set()
    for i, p in enumerate(pairs, start=1):
        pair = str(p.get("pair","")).strip().upper()
        if not pair or "_" not in pair:
            raise ValueError(f"Некорректный PAIR в слоте {i}: '{pair}'")

        ex = str(p.get("exchange","gate")).strip().lower() or "gate"
        if ex not in allowed_ex:
            raise ValueError(f"Некорректная биржа в слоте {i}: '{ex}'. Допустимо: {sorted(allowed_ex)}")

        key = (ex, pair)
        if key in seen_pairs:
            raise ValueError(f"Дубликат пары для биржи {ex}: {pair}")
        seen_pairs.add(key)

        if "gap_switch_pct" in p:
            gs = Decimal(str(p.get("gap_switch_pct","1")))
        else:
            gs = Decimal("1")

        norm.append(PairCfg(
            idx=i,
            exchange=ex,
            pair=pair,
            deviation_pct=Decimal(str(p.get("deviation_pct","0"))),
            quote=Decimal(str(p.get("quote","0"))),
            lot_size_base=Decimal(str(p.get("lot_size_base","0"))),
            gap_mode=str(p.get("gap_mode","down_only")).lower(),
            gap_switch_pct=gs,
            enabled=bool(p.get("enabled", True)),
        ))

    conn = get_conn()
    has_ex = _has_column(conn, "bot_pairs", "exchange")
    cur = None
    try:
        cur = conn.cursor()
        # Полная замена набора
        cur.execute("DELETE FROM bot_pairs;")
        is_sqlite = _is_sqlite_conn(conn)
        for p in norm:
            if has_ex:
                if is_sqlite:
                    cur.execute(
                        "INSERT INTO bot_pairs(idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled, exchange) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (p["idx"], p["pair"], str(p["deviation_pct"]), str(p["quote"]), str(p["lot_size_base"]),
                         p["gap_mode"], str(p["gap_switch_pct"]), 1 if p["enabled"] else 0, p["exchange"])
                    )
                else:
                    cur.execute(
                        "INSERT INTO bot_pairs(idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled, exchange) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (p["idx"], p["pair"], str(p["deviation_pct"]), str(p["quote"]), str(p["lot_size_base"]),
                         p["gap_mode"], str(p["gap_switch_pct"]), True if p["enabled"] else False, p["exchange"])
                    )
            else:
                if is_sqlite:
                    cur.execute(
                        "INSERT INTO bot_pairs(idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (p["idx"], p["pair"], str(p["deviation_pct"]), str(p["quote"]), str(p["lot_size_base"]),
                         p["gap_mode"], str(p["gap_switch_pct"]), 1 if p["enabled"] else 0)
                    )
                else:
                    cur.execute(
                        "INSERT INTO bot_pairs(idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (p["idx"], p["pair"], str(p["deviation_pct"]), str(p["quote"]), str(p["lot_size_base"]),
                         p["gap_mode"], str(p["gap_switch_pct"]), True if p["enabled"] else False)
                    )
    finally:
        try:
            if cur is not None: cur.close()
        except Exception:
            pass

    return list_pairs(include_disabled=True)

def _resequence_pairs(conn) -> None:
    """
    Устойчиво перенумеровывает idx: если есть колонка exchange — учитываем её,
    чтобы одинаковые пары на разных биржах не конфликтовали.
    """
    cur = conn.cursor()
    try:
        has_ex = _has_column(conn, "bot_pairs", "exchange")
        if has_ex:
            # читаем обе колонки, упорядочим стабильно (сначала текущий idx, затем exchange/pair)
            cur.execute("SELECT idx, exchange, pair FROM bot_pairs ORDER BY idx ASC, exchange ASC, pair ASC;")
            rows = cur.fetchall() or []
            for new_idx, row in enumerate(rows, start=1):
                _old_idx, ex, pr = row
                if _is_sqlite_conn(conn):
                    cur.execute("UPDATE bot_pairs SET idx=? WHERE pair=? AND LOWER(exchange)=LOWER(?)", (new_idx, pr, ex))
                else:
                    cur.execute("UPDATE bot_pairs SET idx=%s WHERE pair=%s AND LOWER(exchange)=LOWER(%s)", (new_idx, pr, ex))
        else:
            # старая схема — переносим по pair
            cur.execute("SELECT idx, pair FROM bot_pairs ORDER BY idx ASC, pair ASC;")
            rows = cur.fetchall() or []
            pairs_sorted = [r[1] for r in rows]
            for new_idx, pair in enumerate(pairs_sorted, start=1):
                cur.execute(
                    "UPDATE bot_pairs SET idx=%s WHERE pair=%s;" if not _is_sqlite_conn(conn)
                    else "UPDATE bot_pairs SET idx=? WHERE pair=?;",
                    (new_idx, pair)
                )
    finally:
        try: cur.close()
        except Exception: pass

def delete_pair(exchange: str, pair: str) -> bool:
    """
    Удаляет запись пары из bot_pairs с учётом мультибиржи.
    Возвращает True, если что-то удалено.
    """
    conn = get_conn()
    cur = conn.cursor()
    has_ex = _has_column(conn, "bot_pairs", "exchange")
    try:
        if has_ex:
            # точное совпадение по бирже и паре
            if _is_sqlite_conn(conn):
                cur.execute("DELETE FROM bot_pairs WHERE pair = ? AND LOWER(exchange)=LOWER(?)", (pair, exchange))
            else:
                cur.execute("DELETE FROM bot_pairs WHERE pair = %s AND LOWER(exchange)=LOWER(%s)", (pair, exchange))
        else:
            # старая БД — без колонки exchange: удаляем по pair
            if _is_sqlite_conn(conn):
                cur.execute("DELETE FROM bot_pairs WHERE pair = ?", (pair,))
            else:
                cur.execute("DELETE FROM bot_pairs WHERE pair = %s", (pair,))
        deleted = cur.rowcount if hasattr(cur, "rowcount") else 0
        _resequence_pairs(conn)
        return deleted > 0
    finally:
        try: cur.close()
        except Exception: pass
