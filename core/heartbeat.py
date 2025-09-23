# core/heartbeat.py
from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Optional

from core.db import get_conn
from core.params import get_paused, list_pairs
from core.telemetry import send_event

# Ключи в bot_runtime
RT_LAST_TICK      = "hb_last_tick"       # последняя «живая» отметка цикла (сек)
RT_LAST_PING_SENT = "hb_last_ping_sent"  # когда отправили последний heartbeat (сек)

# Параметры heartbeat
HEARTBEAT_EVERY_SEC = 60 * 60             # раз в 60 минут
SILENCE_ALERT_SEC   = int(1.5 * 60 * 60)  # если тишина > 90 минут — шлём алерт при старте

def _is_sqlite_conn(conn) -> bool:
    try:
        return conn.__class__.__module__.startswith("sqlite3")
    except Exception:
        return (not hasattr(conn, "closed")) and hasattr(conn, "execute")

def _rt_get(key: str) -> Optional[int]:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM bot_runtime WHERE key=%s;" if not _is_sqlite_conn(conn)
                    else "SELECT value FROM bot_runtime WHERE key=?;", (key,))
        row = cur.fetchone()
        if not row:
            return None
        v = row[0] if isinstance(row, (list, tuple)) else row
        try:
            return int(v)
        except Exception:
            return None
    finally:
        try: cur.close()
        except Exception: pass

def _rt_set(key: str, value: int) -> None:
    conn = get_conn()
    is_sqlite = _is_sqlite_conn(conn)
    cur = conn.cursor()
    try:
        if is_sqlite:
            cur.execute("INSERT OR REPLACE INTO bot_runtime(key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                        (key, str(int(value))))
        else:
            cur.execute(
                "INSERT INTO bot_runtime(key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
                (key, str(int(value)))
            )
    finally:
        try: cur.close()
        except Exception: pass

def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def init():
    """
    Вызывается один раз при старте воркера.
    1) Проверяет «тишину» > SILENCE_ALERT_SEC и шлёт alert при необходимости.
    2) Отправляет стартовый heartbeat немедленно (как и было у тебя ранее).
    """
    now = int(time.time())
    last_tick = _rt_get(RT_LAST_TICK)
    if last_tick is not None:
        gap = now - last_tick
        if gap > SILENCE_ALERT_SEC:
            paused = get_paused()
            pairs = list_pairs(include_disabled=False)
            msg = (
                f"<b>Долгая тишина обнаружена</b>\n"
                f"• Последняя активность: { _fmt_ts(last_tick) }\n"
                f"• Длительность простоя: {gap//60} мин\n"
                f"• Пауза: {'да' if paused else 'нет'}\n"
                f"• Активных пар: {len(pairs)}"
            )
            send_event("alert_silence", msg)

    # Зафиксируем «живой» тик
    _rt_set(RT_LAST_TICK, now)

    # 🔔 Стартовый heartbeat — чтобы сообщение пришло в первую минуту старта
    paused = get_paused()
    pairs = list_pairs(include_disabled=False)
    start_msg = (
        f"<b>Heartbeat (startup)</b>\n"
        f"• Время: { _fmt_ts(now) }\n"
        f"• Пауза: {'да' if paused else 'нет'}\n"
        f"• Активных пар: {len(pairs)}"
    )
    send_event("heartbeat", start_msg)
    # помечаем, что пинг уже отправлен — следующий будет через HEARTBEAT_EVERY_SEC
    _rt_set(RT_LAST_PING_SENT, now)

def tick():
    """
    Вызывается раз в цикл (обычно раз в минуту).
    1) Обновляет «последний тик».
    2) Если прошло >= HEARTBEAT_EVERY_SEC с последнего отправленного пинга — шлёт heartbeat.
    """
    now = int(time.time())
    _rt_set(RT_LAST_TICK, now)

    last_sent = _rt_get(RT_LAST_PING_SENT) or 0
    if now - last_sent >= HEARTBEAT_EVERY_SEC:
        paused = get_paused()
        pairs = list_pairs(include_disabled=False)
        msg = (
            f"<b>Heartbeat</b>\n"
            f"• Время: { _fmt_ts(now) }\n"
            f"• Пауза: {'да' if paused else 'нет'}\n"
            f"• Активных пар: {len(pairs)}"
        )
        send_event("heartbeat", msg)
        _rt_set(RT_LAST_PING_SENT, now)
