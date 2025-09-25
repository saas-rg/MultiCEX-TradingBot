# core/reporting.py
from __future__ import annotations
from decimal import Decimal
from typing import Tuple, Dict, Any, List
import time, csv, io
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import threading

from core.db import get_conn
from core.params import list_pairs, get_paused
from core.quant import fmt
from core.telemetry import send_event, send_document

# ========== Ключи настроек/рантайма ==========
SETTINGS_KEY_ENABLED     = "REPORT_ENABLED"
SETTINGS_KEY_PERIOD_MIN  = "REPORT_PERIOD_MIN"       # 1|5|10|15|30|60
RUNTIME_KEY_LAST_END_TS  = "report_last_period_end"  # unix seconds конца ПРЕДЫДУЩЕГО завершенного периода

# ======== HTTP-обёртка проекта (подписанные запросы) ========
try:
    from core.http import request as http_request  # type: ignore
except Exception:
    http_request = None

# ======== Фоновый исполнитель для отчётов (НЕ блокирует торговлю) ========
_BG_EXEC = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reporting")
_BG_LOCK = threading.Lock()  # защитимся от двойного планирования в одну и ту же минуту

def _fetch_trades_gate(currency_pair: str, ts_from: int, ts_to: int, limit: int = 1000) -> List[Dict[str, Any]]:
    """
    Считывает сделки пользователя с Gate API v4: GET /spot/my_trades (SIGNED).
    Для нашей обёртки request(...) query нужно передавать параметром `query`.
    """
    if http_request is None:
        return []
    params = {
        "currency_pair": currency_pair,
        "from": str(max(0, ts_from)),
        "to": str(max(ts_from, ts_to)),
        "limit": str(limit),
    }
    resp = http_request("GET", "/spot/my_trades", query=params, signed=True)  # type: ignore
    if not isinstance(resp, (list, tuple)):
        return []
    out: List[Dict[str, Any]] = []
    for it in resp:
        try:
            out.append({
                "id": str(it.get("id", "")),
                "side": str(it.get("side", "")),
                "amount": Decimal(str(it.get("amount", "0"))),
                "price":  Decimal(str(it.get("price", "0"))),
                "create_time": int(float(it.get("create_time", it.get("create_time_ms", 0)))),
                "fee": Decimal(str(it.get("fee", "0"))),
                "fee_currency": str(it.get("fee_currency", "")),
            })
        except Exception:
            continue
    return out

# ========== Утилиты БД ==========
def _is_sqlite_conn(conn) -> bool:
    try:
        return conn.__class__.__module__.startswith("sqlite3")
    except Exception:
        return (not hasattr(conn, "closed")) and hasattr(conn, "execute")

def _kv_get(key: str) -> str | None:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM bot_settings WHERE key=%s;" if not _is_sqlite_conn(conn) else
                    "SELECT value FROM bot_settings WHERE key=?;", (key,))
        row = cur.fetchone()
        if not row:
            return None
        return row[0] if isinstance(row, (list, tuple)) else row
    finally:
        try: cur.close()
        except Exception: pass

def _kv_set(key: str, value: str) -> None:
    conn = get_conn()
    is_sqlite = _is_sqlite_conn(conn)
    cur = conn.cursor()
    try:
        if is_sqlite:
            cur.execute("INSERT OR REPLACE INTO bot_settings(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (key, value))
        else:
            cur.execute("INSERT INTO bot_settings(key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (key, value))
    finally:
        try: cur.close()
        except Exception: pass

def _rt_get(key: str) -> str | None:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM bot_runtime WHERE key=%s;" if not _is_sqlite_conn(conn) else
                    "SELECT value FROM bot_runtime WHERE key=?;", (key,))
        row = cur.fetchone()
        if not row:
            return None
        return row[0] if isinstance(row, (list, tuple)) else row
    finally:
        try: cur.close()
        except Exception: pass

def _rt_set(key: str, value: str) -> None:
    conn = get_conn()
    is_sqlite = _is_sqlite_conn(conn)
    cur = conn.cursor()
    try:
        if is_sqlite:
            cur.execute("INSERT OR REPLACE INTO bot_runtime(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)", (key, value))
        else:
            cur.execute("INSERT INTO bot_runtime(key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()", (key, value))
    finally:
        try: cur.close()
        except Exception: pass

# ========== Настройки отчётов ==========
def _normalize_period(p: int) -> int:
    allowed = (1,5,10,15,30,60)
    return p if p in allowed else 60

def get_settings() -> Tuple[bool, int]:
    enabled = False
    period_min = 60
    v = _kv_get(SETTINGS_KEY_ENABLED)
    if v is not None:
        enabled = str(v).lower() in ("1","true","yes","y","on")
    v = _kv_get(SETTINGS_KEY_PERIOD_MIN)
    if v is not None:
        try:
            period_min = int(str(v))
        except Exception:
            period_min = 60
    return enabled, _normalize_period(period_min)

def set_settings(enabled: bool, period_min: int) -> Tuple[bool, int]:
    period_min = _normalize_period(period_min)
    _kv_set(SETTINGS_KEY_ENABLED, "true" if enabled else "false")
    _kv_set(SETTINGS_KEY_PERIOD_MIN, str(period_min))
    return get_settings()

# ========== Временные правила и окна ==========
def _floor_minute_utc(ts: int) -> int:
    return (ts // 60) * 60

def _align_period_end(ts: int, period_min: int) -> int:
    """Конец последнего завершенного периода (E=...:59) по UTC."""
    k = period_min * 60
    m0 = _floor_minute_utc(ts)
    end = ((m0 // k) * k) - 1
    return max(0, end)

def _period_bounds_by_end(end_ts: int, period_min: int) -> Tuple[int,int]:
    k = period_min * 60
    start = end_ts - (k - 1)
    return start, end_ts

def _buy_sell_windows(start_ts: int, end_ts: int) -> Tuple[Tuple[int,int], Tuple[int,int]]:
    # SELL: [S, E], BUY: [S-60, E-60] — обе границы включительно
    return (start_ts - 60, end_ts - 60), (start_ts, end_ts)

def _is_first_minute_after(end_ts: int, now_ts: int) -> bool:
    first_minute_start = (end_ts + 1) // 60 * 60
    return first_minute_start <= now_ts <= first_minute_start + 59

# ========== Сбор сделок ==========
def _collect_trades_for_pairs(pairs: List[Dict[str, Any]], buy_win: Tuple[int,int], sell_win: Tuple[int,int]) -> List[Dict[str, Any]]:
    """
    Собираем сделки по всем парам в нужных окнах. Здесь ещё не меняем знак quote_value — это сделаем в CSV/подсчёте.
    """
    rows: List[Dict[str, Any]] = []
    for p in pairs:
        pair = p["pair"]
        exch = p.get("exchange", "gate")  # v0.7.2: биржа пары (по умолчанию gate)
        base_sym = pair.split("_", 1)[0] if "_" in pair else pair
        # BUY
        for tr in _fetch_trades_gate(pair, buy_win[0], buy_win[1]):
            if tr.get("side") != "buy":
                continue
            ts = int(tr["create_time"])
            rows.append({
                "ts": ts,
                "ts_iso": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "exchange": exch,                 # <-- добавлено
                "pair": pair,
                "base": base_sym,
                "side": "BUY",
                "price": tr["price"],
                "amount": tr["amount"],
                "fee": tr.get("fee", Decimal("0")),
                "fee_currency": tr.get("fee_currency", ""),
                "id": tr.get("id","")
            })
        # SELL
        for tr in _fetch_trades_gate(pair, sell_win[0], sell_win[1]):
            if tr.get("side") != "sell":
                continue
            ts = int(tr["create_time"])
            rows.append({
                "ts": ts,
                "ts_iso": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "exchange": exch,                 # <-- добавлено
                "pair": pair,
                "base": base_sym,
                "side": "SELL",
                "price": tr["price"],
                "amount": tr["amount"],
                "fee": tr.get("fee", Decimal("0")),
                "fee_currency": tr.get("fee_currency", ""),
                "id": tr.get("id","")
            })
    rows.sort(key=lambda r: (r["ts"], r["id"]))
    return rows

def _fee_to_usdt(side: str, base: str, fee: Decimal, fee_currency: str, price: Decimal) -> Decimal:
    """
    Конвертируем комиссию в USDT:
    - если fee_currency == USDT -> как есть
    - если fee_currency == base -> fee * price
    - иначе (например, GT) -> 0 (не учитываем в сумме, курс неизвестен)
    """
    if fee <= 0:
        return Decimal("0")
    if fee_currency.upper() == "USDT":
        return fee
    if fee_currency.upper() == base.upper():
        try:
            return (fee * price)
        except Exception:
            return Decimal("0")
    return Decimal("0")

# ========== Текст отчёта (с NET) ==========
def build_report_text(period_min: int, ref_end_ts: int) -> str:
    paused = get_paused()
    pairs = list_pairs(include_disabled=True)

    S, E = _period_bounds_by_end(ref_end_ts, period_min)
    (buy_s, buy_e), (sell_s, sell_e) = _buy_sell_windows(S, E)

    def ts_fmt(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Подсчёт NET (та же логика, что и в CSV)
    rows = _collect_trades_for_pairs(pairs, (buy_s, buy_e), (sell_s, sell_e))
    total_quote = Decimal("0")
    total_fee_usdt = Decimal("0")
    for r in rows:
        price  = Decimal(str(r["price"]))
        amount = Decimal(str(r["amount"]))
        fee    = Decimal(str(r.get("fee","0")))
        base   = str(r.get("base",""))
        side   = r["side"]
        qv = (amount * price)
        if side == "BUY":
            qv = -qv
        fee_usdt = _fee_to_usdt(side, base, fee, str(r.get("fee_currency","")), price)
        total_quote += qv
        total_fee_usdt += fee_usdt
    net = total_quote - total_fee_usdt

    lines: List[str] = []
    lines.append(f"<b>Отчёт за период {period_min} мин</b>")
    lines.append(f"• SELL: {ts_fmt(sell_s)} — {ts_fmt(sell_e)} (включ.)")
    lines.append(f"• BUY:  {ts_fmt(buy_s)} — {ts_fmt(buy_e)} (включ.)")
    lines.append(f"<b>Статус бота:</b> {'⏸️ пауза' if paused else '▶️ работает'}")
    lines.append(f"Всего пар: {len(pairs)}; активных: {sum(1 for p in pairs if p.get('enabled'))}")
    # Итог NET (USDT) — дублирует CSV
    lines.append(f"<b>Итог NET (USDT):</b> {fmt(net, 6)}")
    for p in pairs:
        exch = p.get("exchange", "gate")  # v0.7.2: показываем биржу в телеметрии
        lines.append(
            "• [{ex}:{pair}] dev={dev}% {mode}/{gs}% {lot_or_quote} {en}".format(
                ex=exch,
                pair=p["pair"],
                dev=fmt(p["deviation_pct"], 3),
                mode=p["gap_mode"],
                gs=fmt(p["gap_switch_pct"], 2),
                lot_or_quote=("LOT="+fmt(p["lot_size_base"],8)) if Decimal(str(p["lot_size_base"]))>0 else ("QUOTE="+fmt(p["quote"],2)),
                en=("✅" if p.get("enabled") else "🚫")
            )
        )
    return "\n".join(lines)

# ========== CSV с итоговой строкой ==========
def build_report_csv(period_min: int, ref_end_ts: int) -> bytes:
    pairs = list_pairs(include_disabled=True)
    S, E = _period_bounds_by_end(ref_end_ts, period_min)
    (buy_s, buy_e), (sell_s, sell_e) = _buy_sell_windows(S, E)

    rows = _collect_trades_for_pairs(pairs, (buy_s, buy_e), (sell_s, sell_e))

    total_quote = Decimal("0")
    total_fee_usdt = Decimal("0")

    buf = io.StringIO()
    wr = csv.writer(buf)
    # v0.7.2: добавили колонку exchange (третьей)
    wr.writerow(["ts","ts_iso","exchange","pair","side","price","amount","quote_value","fee","fee_currency","trade_id"])

    for r in rows:
        price  = Decimal(str(r["price"]))
        amount = Decimal(str(r["amount"]))
        fee    = Decimal(str(r.get("fee", "0")))
        base   = str(r.get("base",""))
        side   = r["side"]

        # quote_value: BUY отрицательный, SELL положительный
        qv = (amount * price)
        if side == "BUY":
            qv = -qv

        # fee в USDT (накапливаем для NET)
        fee_usdt = _fee_to_usdt(side, base, fee, str(r.get("fee_currency","")), price)

        total_quote += qv
        total_fee_usdt += fee_usdt

        wr.writerow([
            r["ts"], r["ts_iso"], r.get("exchange","gate"), r["pair"], side,
            str(price), str(amount), str(qv),
            str(fee), r.get("fee_currency",""), r["id"]
        ])

    net = total_quote - total_fee_usdt
    # Итоговая строка (оставляем формат как был; без "exchange")
    wr.writerow([
        "TOTAL", "", "ALL", "NET",
        "", "", str(net),
        str(total_fee_usdt), "USDT", ""
    ])

    return buf.getvalue().encode("utf-8")

# ========== Отправка/тик ==========
def _get_last_period_end_ts() -> int:
    v = _rt_get(RUNTIME_KEY_LAST_END_TS)
    try:
        return int(v) if v is not None else 0
    except Exception:
        return 0

def _set_last_period_end_ts(ts_val: int) -> None:
    _rt_set(RUNTIME_KEY_LAST_END_TS, str(int(ts_val)))

def _build_and_send(period_min: int, end_ts: int) -> None:
    """
    Фактическая сборка + отправка отчёта (в фоне).
    Никаких исключений наружу не выбрасываем.
    """
    try:
        text = build_report_text(period_min, end_ts)
        csv_bytes = build_report_csv(period_min, end_ts)
        send_event("report", text)
        ts_label = datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        send_document(f"trades_{period_min}m_until_{ts_label}.csv", csv_bytes, caption="CSV сделок за отчётный период")
    except Exception:
        # тихо гасим любые ошибки, чтобы не мешать торговле
        pass

def _schedule_background_report(period_min: int, end_ts: int) -> None:
    """
    Планирует отправку отчёта в отдельном потоке.
    ВАЖНО: помечаем период как «отправленный» СРАЗУ, чтобы исключить дублирование.
    """
    _set_last_period_end_ts(end_ts)
    _BG_EXEC.submit(_build_and_send, period_min, end_ts)

def send_report(force: bool = False) -> bool:
    """
    Синхронная отправка отчёта (используется только веб-кнопкой /reporting/send).
    Торговый цикл её не вызывает.
    """
    enabled, period_min = get_settings()
    if not enabled and not force:
        return False
    now = int(time.time())
    last_completed_end = _align_period_end(now, period_min)

    if not force:
        if not _is_first_minute_after(last_completed_end, now):
            return False
        if _get_last_period_end_ts() == last_completed_end:
            return False

    text = build_report_text(period_min, last_completed_end)
    csv_bytes = build_report_csv(period_min, last_completed_end)
    send_event("report", text)
    ts_label = datetime.fromtimestamp(last_completed_end, tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    send_document(f"trades_{period_min}m_until_{ts_label}.csv", csv_bytes, caption="CSV сделок за отчётный период")
    _set_last_period_end_ts(last_completed_end)
    return True

def tick():
    """
    НЕ блокирует торговлю. Если настало «окно» первой минуты — планирует отчёт
    в фоне и сразу возвращается.
    """
    try:
        enabled, period_min = get_settings()
        if not enabled:
            return
        now = int(time.time())
        end_ts = _align_period_end(now, period_min)
        if _is_first_minute_after(end_ts, now):
            with _BG_LOCK:
                if _get_last_period_end_ts() != end_ts:
                    _schedule_background_report(period_min, end_ts)
    except Exception:
        pass
