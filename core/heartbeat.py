# core/heartbeat.py
from __future__ import annotations
import time
import threading
from datetime import datetime, timezone
from typing import Optional

from core.db import get_conn
from core.params import get_paused, list_pairs
from core.telemetry import send_event

# Ключи в bot_runtime
RT_LAST_TICK       = "hb_last_tick"        # последняя «живая» отметка цикла (сек) — для 60/90-мин логики
RT_LAST_PING_SENT  = "hb_last_ping_sent"   # когда отправили последний heartbeat в TG (сек)
RT_LAST_FAST_PING  = "hb_last_fast_ping"   # быстрый пинг (каждые N сек) — для статуса админки

# Параметры heartbeat (минутная логика TG)
HEARTBEAT_EVERY_SEC = 60 * 60              # раз в 60 минут отправлять heartbeat в TG
SILENCE_ALERT_SEC   = int(1.5 * 60 * 60)   # если тишина > 90 минут — шлём алерт при старте

# Фоновый поток fast-ping’а
_fast_ping_thread: Optional[threading.Thread] = None
_fast_ping_interval_sec: int = 5  # по умолчанию 5 сек


def _is_sqlite_conn(conn) -> bool:
    try:
        return conn.__class__.__module__.startswith("sqlite3")
    except Exception:
        return (not hasattr(conn, "closed")) and hasattr(conn, "execute")


def _rt_get(key: str) -> Optional[int]:
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT value FROM bot_runtime WHERE key=%s;" if not _is_sqlite_conn(conn)
            else "SELECT value FROM bot_runtime WHERE key=?;",
            (key,)
        )
        row = cur.fetchone()
        if not row:
            return None
        v = row[0] if isinstance(row, (list, tuple)) else row
        try:
            return int(str(v))
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
            cur.execute(
                "INSERT OR REPLACE INTO bot_runtime(key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (key, str(int(value)))
            )
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


def _fast_ping_once(ts: Optional[int] = None) -> None:
    """Одноразовый быстрый пинг для веб-админки."""
    if ts is None:
        ts = int(time.time())
    _rt_set(RT_LAST_FAST_PING, ts)


def start_fast_ping_loop(interval_sec: int = 5) -> None:
    """
    Запускает фоновый поток, который обновляет RT_LAST_FAST_PING каждые interval_sec.
    Безопасно вызывается многократно — второй раз не стартует.
    """
    global _fast_ping_thread, _fast_ping_interval_sec
    _fast_ping_interval_sec = max(1, int(interval_sec))

    if _fast_ping_thread and _fast_ping_thread.is_alive():
        return

    def _loop():
        while True:
            try:
                _fast_ping_once()
            except Exception:
                pass
            time.sleep(_fast_ping_interval_sec)

    t = threading.Thread(target=_loop, name="fast-ping", daemon=True)
    _fast_ping_thread = t
    t.start()


def get_last_ping_ts() -> Optional[int]:
    """Для веб-админки: последнее значение RT_LAST_FAST_PING (сек, UTC)."""
    return _rt_get(RT_LAST_FAST_PING)


def init(ping_interval_sec: int = 5):
    """
    Вызывается при старте воркера.
    1) Проверяет «тишину» > SILENCE_ALERT_SEC и шлёт alert при необходимости.
    2) Фиксирует живой тик.
    3) Отправляет стартовый heartbeat (как раньше).
    4) Запускает fast-ping петлю для статуса админки.
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

    # Зафиксируем «живой» тик (для 60/90-мин логики)
    _rt_set(RT_LAST_TICK, now)

    # Стартовый heartbeat в TG — чтобы пришло сразу после запуска
    paused = get_paused()
    pairs = list_pairs(include_disabled=False)
    start_msg = (
        f"<b>Heartbeat (startup)</b>\n"
        f"• Время: { _fmt_ts(now) }\n"
        f"• Пауза: {'да' if paused else 'нет'}\n"
        f"• Активных пар: {len(pairs)}"
    )
    send_event("heartbeat", start_msg)
    _rt_set(RT_LAST_PING_SENT, now)

    # Быстрые пинги для статуса админки
    start_fast_ping_loop(ping_interval_sec)


def tick():
    """
    Вызывается раз в цикл (обычно раз в минуту).
    1) Обновляет «последний тик» (для 60/90-мин логики).
    2) Если прошло >= HEARTBEAT_EVERY_SEC с последнего отправленного пинга — шлёт heartbeat в TG.
    (Fast-ping обновляется фоновым потоком и никак не влияет на TG-правила.)
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
