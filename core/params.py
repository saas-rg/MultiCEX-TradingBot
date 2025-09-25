# core/params.py
from decimal import Decimal
from typing import Dict, Any, List, TypedDict, Literal
from config import (
    PAIR, DEVIATION_PCT, QUOTE_USDT, LOT_SIZE_BASE, GAP_MODE, GAP_SWITCH_PCT,
)
from .db import get_conn, init_db

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
    # v0.7.2 ↓ добавлено поле биржи (пока дефолт — "gate")
    exchange: str  # "gate" (для совместимости; миграции БД пока нет)

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

def ensure_schema():
    init_db()
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM bot_pairs;")
        row = cur.fetchone()
        cnt = int(row[0]) if row else 0
        if cnt == 0:
            if _is_sqlite_conn(conn):
                cur.execute(
                    "INSERT INTO bot_pairs(idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (1, PAIR, str(DEVIATION_PCT), str(QUOTE_USDT), str(LOT_SIZE_BASE), GAP_MODE, str(GAP_SWITCH_PCT), 1)
                )
            else:
                cur.execute(
                    "INSERT INTO bot_pairs(idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (1, PAIR, str(DEVIATION_PCT), str(QUOTE_USDT), str(LOT_SIZE_BASE), GAP_MODE, str(GAP_SWITCH_PCT), True)
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

def list_pairs(include_disabled: bool = False) -> List[PairCfg]:
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        if include_disabled:
            cur.execute("SELECT idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled FROM bot_pairs ORDER BY idx ASC")
        else:
            if _is_sqlite_conn(conn):
                cur.execute("SELECT idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled FROM bot_pairs WHERE enabled = 1 ORDER BY idx ASC")
            else:
                cur.execute("SELECT idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled FROM bot_pairs WHERE enabled = %s ORDER BY idx ASC", (True,))
        rows = cur.fetchall()
    finally:
        try:
            if cur is not None: cur.close()
        except Exception: pass

    out: List[PairCfg] = []
    for r in rows:
        cfg = PairCfg(
            idx=int(r[0]),
            pair=str(r[1]),
            deviation_pct=Decimal(str(r[2])),
            quote=Decimal(str(r[3])),
            lot_size_base=Decimal(str(r[4])),
            gap_mode=str(r[5]),
            gap_switch_pct=Decimal(str(r[6])),
            enabled=bool(int(r[7])) if not isinstance(r[7], bool) else bool(r[7]),
        )
        # v0.7.2 ↓ аккуратно добавляем биржу по умолчанию (Gate), без миграции БД
        cfg["exchange"] = "gate"
        out.append(cfg)
    return out

def upsert_pairs(pairs: List[PairCfg]) -> List[PairCfg]:
    if len(pairs) > 5:
        raise ValueError("Можно задать не более 5 пар")
    norm: List[PairCfg] = []
    seen_pairs: set[str] = set()
    for i, p in enumerate(pairs, start=1):
        pair = str(p.get("pair","")).strip().upper()
        if not pair or "_" not in pair:
            raise ValueError(f"Некорректный PAIR в слоте {i}: '{pair}'")
        if pair in seen_pairs:
            raise ValueError(f"Дубликат пары: {pair}")
        seen_pairs.add(pair)
        norm.append(PairCfg(
            idx=i,
            pair=pair,
            deviation_pct=Decimal(str(p.get("deviation_pct","0"))),
            quote=Decimal(str(p.get("quote","0"))),
            lot_size_base=Decimal(str(p.get("lot_size_base","0"))),
            gap_mode=str(p.get("gap_mode","down_only")).lower(),
            gap_switch_pct=Decimal(str(p.get("gap_switch_pct","1"))),
            enabled=bool(p.get("enabled", True)),
            # exchange намеренно не пишем в БД в v0.7.2 (нет миграции); на чтении ставим "gate"
        ))

    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM bot_pairs;")
        is_sqlite = _is_sqlite_conn(conn)
        for p in norm:
            if is_sqlite:
                cur.execute(
                    "INSERT INTO bot_pairs(idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled) VALUES (?,?,?,?,?,?,?,?)",
                    (p["idx"], p["pair"], str(p["deviation_pct"]), str(p["quote"]), str(p["lot_size_base"]),
                     p["gap_mode"], str(p["gap_switch_pct"]), 1 if p["enabled"] else 0)
                )
            else:
                cur.execute(
                    "INSERT INTO bot_pairs(idx, pair, deviation_pct, quote, lot_size_base, gap_mode, gap_switch_pct, enabled) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (p["idx"], p["pair"], str(p["deviation_pct"]), str(p["quote"]), str(p["lot_size_base"]),
                     p["gap_mode"], str(p["gap_switch_pct"]), True)
                )
    finally:
        try:
            if cur is not None: cur.close()
        except Exception: pass
    return list_pairs(include_disabled=True)
