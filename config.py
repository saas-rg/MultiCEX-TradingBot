# config.py
import os
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()

# =========================
# Унифицированная конфигурация CEX:
# - EXCH_LIST = "gate,htx" (по умолчанию)
# - Для каждой {code} читаем:
#     {CODE}_API_KEY
#     {CODE}_API_SECRET
#     {CODE}_HOST            (опционально; у известных CEX подставим дефолты)
#     {CODE}_ACCOUNT_TYPE    (например "spot")
#     {CODE}_USE_SDK         ("true"/"false") — попытка включить SDK для этой биржи
# - Реестр EXCHANGES хранит все настройки + (опционально) инициализированный SDK.
# - get_exchange_cfg("gate"|"htx"|...) возвращает словарь для адаптера.
# - DEFAULT_EXCHANGE = "gate"
# - Обратная совместимость: глобальные API_KEY/HOST/... указывают на Gate.
# =========================

# ---------- Утилиты ----------
def _as_bool(v: str, default: bool = False) -> bool:
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")

TESTNET = _as_bool(os.getenv("TESTNET", "true"), True)  # влияет на дефолтный HOST Gate

# ---------- Список бирж ----------
EXCH_LIST = os.getenv("EXCH_LIST", "gate,htx")
CODES = [c.strip().lower() for c in EXCH_LIST.split(",") if c.strip()]

DEFAULT_EXCHANGE = os.getenv("DEFAULT_EXCHANGE", "gate").strip().lower() or "gate"
if DEFAULT_EXCHANGE not in CODES:
    CODES.insert(0, DEFAULT_EXCHANGE)  # гарантируем присутствие

# ---------- SDK загрузчики по известным биржам ----------
# Каждый loader принимает (cfg: dict) и возвращает объект SDK (или dict клиентов), либо бросает исключение.
SDK_LOADERS = {}

# Gate.io SDK (официальный gate_api)
def _gate_sdk_loader(cfg: dict):
    # Требуются зависимости gate_api
    from gate_api import ApiClient, Configuration
    from gate_api import SpotApi
    api_key = cfg["api_key"]
    api_secret = cfg["api_secret"]
    host = cfg["host"]
    gcfg = Configuration(key=api_key, secret=api_secret)
    gcfg.host = host
    return SpotApi(ApiClient(gcfg))

SDK_LOADERS["gate"] = _gate_sdk_loader

# HTX (Huobi) SDK (де-факто пакет "huobi"). Если пакет недоступен — use_sdk отключится.
def _htx_sdk_loader(cfg: dict):
    # Библиотека huobi (исторически для HTX). Попробуем создать клиентов.
    # Адаптер сможет использовать то, что вернём (например, dict клиентов).
    from huobi.client.market import MarketClient
    from huobi.client.account import AccountClient
    from huobi.client.trade import TradeClient
    api_key = cfg["api_key"]
    api_secret = cfg["api_secret"]
    host = cfg["host"]
    return {
        "market": MarketClient(),
        "account": AccountClient(api_key=api_key, secret_key=api_secret),
        "trade": TradeClient(api_key=api_key, secret_key=api_secret),
        "host": host,
    }

SDK_LOADERS["htx"] = _htx_sdk_loader

# ---------- Дефолты HOST для известных бирж ----------
def _default_host(code: str) -> str:
    if code == "gate":
        return "https://api-testnet.gateapi.io/api/v4" if TESTNET else "https://api.gateio.ws/api/v4"
    if code == "htx":
        # Спотовый публичный REST у HTX (ранее Huobi). При необходимости поменяешь на актуальный.
        return os.getenv("HTX_HOST", "https://api.huobi.pro").strip() or "https://api.huobi.pro"
    # Для неизвестных бирж — без дефолта
    return os.getenv(f"{code.upper()}_HOST", "").strip()

# ---------- Сборка реестра EXCHANGES ----------
EXCHANGES: dict[str, dict] = {}

for code in CODES:
    U = code.upper()
    api_key = os.getenv(f"{U}_API_KEY", "").strip()
    api_secret = os.getenv(f"{U}_API_SECRET", "").strip()
    host = os.getenv(f"{U}_HOST", "").strip() or _default_host(code)
    account_type = os.getenv(f"{U}_ACCOUNT_TYPE", "").strip() or None
    want_sdk = _as_bool(os.getenv(f"{U}_USE_SDK", "true"), True)  # по умолчанию пытаемся включить SDK
    prefix = os.getenv(f"{U}_PREFIX", "").strip()  # для совместимости (Gate использует /api/v4)

    # Спец-логика: для Gate оставим исторический prefix, если не задан явно
    if code == "gate" and not prefix:
        prefix = "/api/v4"

    entry = {
        "code": code,
        "api_key": api_key,
        "api_secret": api_secret,
        "host": host,
        "account_type": account_type,  # напр. "spot"
        "use_sdk": False,              # станет True, если инициализация SDK пройдёт
        "sdk": None,                   # объект SDK или словарь клиентов
        "prefix": prefix,
        # «тестнетность» можно хранить в каждой записи — полезно для Gate
        "testnet": (TESTNET if code == "gate" else _as_bool(os.getenv(f"{U}_TESTNET", "false"), False)),
    }

    # Пытаемся инициализировать SDK, если пользователь не запретил и у нас есть loader
    if want_sdk and api_key and api_secret and code in SDK_LOADERS:
        try:
            entry["sdk"] = SDK_LOADERS[code](entry)
            entry["use_sdk"] = True if entry["sdk"] is not None else False
        except Exception:
            # Если SDK не взлетел — оставляем REST-путь
            entry["sdk"] = None
            entry["use_sdk"] = False

    EXCHANGES[code] = entry

# ---------- Хелпер для адаптеров ----------
def get_exchange_cfg(code: str) -> dict:
    """
    Вернёт словарь настроек для биржи `code` (gate|htx|...).
    Бросит KeyError, если не найдена.
    """
    return EXCHANGES[code.strip().lower()]

# ---------- Обратная совместимость (старый код ориентирован на Gate) ----------
_GATE = EXCHANGES.get("gate", {
    "api_key": "",
    "api_secret": "",
    "host": "",
    "prefix": "/api/v4",
    "account_type": None,
    "use_sdk": False,
    "sdk": None,
})
API_KEY      = _GATE["api_key"]
API_SECRET   = _GATE["api_secret"]
HOST         = _GATE["host"]
PREFIX       = _GATE.get("prefix", "/api/v4")
ACCOUNT_TYPE = _GATE.get("account_type", None)
USE_SDK      = bool(_GATE.get("use_sdk", False))
sdk_spot     = _GATE.get("sdk", None)  # для совместимости с ранним кодом (SpotApi | dict | None)

# ---------- Дефолты стратегии (при автосоздании первой пары в БД) ----------
PAIR           = os.getenv("PAIR", "BTC_USDT")
DEVIATION_PCT  = Decimal(os.getenv("DEVIATION_PCT", "3.0"))
QUOTE_USDT     = Decimal(os.getenv("QUOTE", "0"))
LOT_SIZE_BASE  = Decimal(os.getenv("LOT_SIZE_BASE", "0"))
GAP_MODE       = os.getenv("GAP_MODE", "down_only").lower()  # off | down_only | symmetric
GAP_SWITCH_PCT = Decimal(os.getenv("GAP_SWITCH_PCT", "1.0"))

# ---------- Слив позиции ----------
SELL_DRAIN_SLEEP  = float(os.getenv("SELL_DRAIN_SLEEP", "0.8"))
DRAIN_SLEEP_MAX   = float(os.getenv("DRAIN_SLEEP_MAX", "2.5"))
DRAIN_MAX_SECONDS = float(os.getenv("DRAIN_MAX_SECONDS", "30"))

# ---------- Сеть/тайминги/ретраи ----------
NEXT_BAR_BUFFER_SEC = float(os.getenv("NEXT_BAR_BUFFER_SEC", "1.4"))
REQ_TIMEOUT         = int(os.getenv("REQ_TIMEOUT", "12"))
RETRIES             = int(os.getenv("MAX_RETRIES", "2"))

# ---------- Web Admin ----------
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

# ---------- Телеметрия ----------
TELEMETRY_ENABLED   = _as_bool(os.getenv("TELEMETRY_ENABLED", "true"), True)
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_THREAD_ID  = os.getenv("TELEGRAM_THREAD_ID", "").strip() or None
APP_NAME            = os.getenv("APP_NAME", "").strip() or os.getenv("HEROKU_APP_NAME", "").strip() or "TradingBot"
ENV_NAME            = os.getenv("ENV", "").strip() or ("heroku" if os.getenv("DYNO") else "local")
