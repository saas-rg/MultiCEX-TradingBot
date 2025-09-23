# core/telegram.py
import html
import requests
from typing import Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else None

def _ensure():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured")

def esc(s: str) -> str:
    """Экранирует текст для parse_mode=HTML."""
    return html.escape(s or "", quote=False)

def _post(method: str, data: dict, files: Optional[dict] = None):
    """
    Отправка запроса к Telegram Bot API.
    - Для простых сообщений используем form-encoded (data).
    - Для документов — multipart (files + data).
    """
    _ensure()
    url = f"{_API_BASE}/{method}"
    if files:
        r = requests.post(url, data=data, files=files, timeout=20)
    else:
        r = requests.post(url, data=data, timeout=20)
    try:
        print(f"[TELEGRAM] {method} -> {r.status_code}")
    except Exception:
        pass
    r.raise_for_status()
    return r.json()

def send_text(text: str, parse_mode: str = "HTML"):
    """Базовая отправка текста (по умолчанию HTML). Текст нужно экранировать через esc()."""
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    return _post("sendMessage", data)

def send_info(text: str):
    """Информационное сообщение (ℹ️)."""
    return send_text(f"ℹ️ {text}", parse_mode="HTML")

def send_warning(text: str):
    """Предупреждающее сообщение (⚠️)."""
    return send_text(f"⚠️ {text}", parse_mode="HTML")

def send_error(text: str):
    """Сообщение об ошибке (🛑)."""
    return send_text(f"🛑 {text}", parse_mode="HTML")

def send_document(filename: str, content: bytes, caption: Optional[str] = None, parse_mode: str = "HTML"):
    """Отправка файла (например, CSV) с подписью."""
    files = {
        "document": (filename, content, "text/csv")
    }
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "parse_mode": parse_mode,
    }
    if caption:
        data["caption"] = caption
    return _post("sendDocument", data=data, files=files)
