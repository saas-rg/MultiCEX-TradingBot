# core/telemetry.py
from __future__ import annotations
import os, json, time, requests
from typing import Any, Dict, Optional
from html import escape as _html_escape

TELEMETRY_ENABLED = os.getenv("TELEMETRY_ENABLED", "true").lower() in ("1","true","yes","y")
TG_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT   = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TG_THREAD = os.getenv("TELEGRAM_THREAD_ID", "").strip() or None

APP_NAME  = os.getenv("APP_NAME", "").strip() or os.getenv("HEROKU_APP_NAME", "").strip() or "TradingBot"
ENV_NAME  = os.getenv("ENV", "").strip() or ("heroku" if os.getenv("DYNO") else "local")

def _tg_send(text: str, parse_mode: Optional[str] = "HTML") -> bool:
    if not TELEMETRY_ENABLED or not TG_TOKEN or not TG_CHAT:
        return False
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        payload: Dict[str, Any] = {"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if TG_THREAD:
            payload["message_thread_id"] = int(TG_THREAD)
        r = requests.post(url, json=payload, timeout=15)
        return 200 <= r.status_code < 300
    except Exception:
        return False

def _tg_send_document(filename: str, data: bytes, caption: Optional[str] = None) -> bool:
    if not TELEMETRY_ENABLED or not TG_TOKEN or not TG_CHAT:
        return False
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument"
        files = {"document": (filename, data)}
        data_form: Dict[str, Any] = {"chat_id": TG_CHAT}
        if caption:
            data_form["caption"] = caption
            data_form["parse_mode"] = "HTML"
        if TG_THREAD:
            data_form["message_thread_id"] = int(TG_THREAD)
        r = requests.post(url, data=data_form, files=files, timeout=30)
        return 200 <= r.status_code < 300
    except Exception:
        return False

_EMOJI = {
    # lifecycle
    "worker_start": "🟢",
    "worker_stop":  "🔴",
    "paused_on":    "⏸️",
    "paused_off":   "▶️",
    "error":        "❗",
    "report":       "📊",

    # config/update events
    "pairs_update":     "🧩",
    "params_update":    "🧪",
    "reporting_update": "🗓️",
    "manual_report":    "📤",

    # heartbeat / alerts
    "heartbeat":        "💓",
    "alert_silence":    "🚨",

    # strategy alerts (новые)
    "auto_resize_buy":  "📉",
    "min_quote_guard":  "⚠️",
}

def _escape_html_block(s: str) -> str:
    # Безопасно экранируем &, <, > и оставляем переносы строк
    return _html_escape(s, quote=False)

def send_event(event: str, msg: str, extra: Optional[Dict[str, Any]] = None) -> None:
    ts = int(time.time())
    prefix = _EMOJI.get(event, "ℹ️")
    # Экранируем переменные части для HTML
    app_html = _escape_html_block(APP_NAME)
    env_html = _escape_html_block(ENV_NAME)
    event_html = _escape_html_block(event)
    msg_html = _escape_html_block(msg)

    header = f"{prefix} <b>{app_html}</b> [{env_html}] — <code>{event_html}</code>"
    tail = ""
    if extra:
        try:
            # Преобразуем extra в pretty JSON и экранируем
            extra_json = json.dumps(extra, ensure_ascii=False, indent=2)
            tail = "\n<pre>" + _escape_html_block(extra_json) + "</pre>"
        except Exception:
            pass

    _tg_send(f"{header}\n{msg_html}\n🕒 <code>{ts}</code>{tail}")

def send_document(filename: str, data: bytes, caption: Optional[str] = None) -> bool:
    return _tg_send_document(filename, data, caption)
