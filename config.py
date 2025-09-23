import os
from decimal import Decimal
from dotenv import load_dotenv

# Попытка использовать официальный SDK (опционально)
USE_SDK = True
try:
    import gate_api
    from gate_api import ApiClient, Configuration
    from gate_api import SpotApi
except Exception:
    USE_SDK = False
    gate_api = None
    SpotApi = None
    ApiClient = None
    Configuration = None

load_dotenv()

API_KEY = os.getenv("GATE_API_KEY", "")
API_SECRET = os.getenv("GATE_API_SECRET", "")

TESTNET = os.getenv("TESTNET", "true").lower() in ("1", "true", "yes", "y")
HOST = "https://api-testnet.gateapi.io/api/v4" if TESTNET else "https://api.gateio.ws/api/v4"
PREFIX = "/api/v4"

# --- Дефолты (используются при автосоздании первой пары в БД) ---
PAIR = os.getenv("PAIR", "BTC_USDT")
DEVIATION_PCT = Decimal(os.getenv("DEVIATION_PCT", "3.0"))
QUOTE_USDT = Decimal(os.getenv("QUOTE", "0"))
LOT_SIZE_BASE = Decimal(os.getenv("LOT_SIZE_BASE", "0"))
GAP_MODE = os.getenv("GAP_MODE", "down_only").lower()  # off | down_only | symmetric
GAP_SWITCH_PCT = Decimal(os.getenv("GAP_SWITCH_PCT", "1.0"))

# Слив позиции
SELL_DRAIN_SLEEP = float(os.getenv("SELL_DRAIN_SLEEP", "0.8"))
DRAIN_SLEEP_MAX  = float(os.getenv("DRAIN_SLEEP_MAX", "2.5"))
DRAIN_MAX_SECONDS = float(os.getenv("DRAIN_MAX_SECONDS", "30"))

# Сеть/тайминги/ретраи
NEXT_BAR_BUFFER_SEC = float(os.getenv("NEXT_BAR_BUFFER_SEC", "1.4"))
REQ_TIMEOUT = int(os.getenv("REQ_TIMEOUT", "12"))
RETRIES = int(os.getenv("MAX_RETRIES", "2"))

# Аккаунт
ACCOUNT_TYPE = (os.getenv("ACCOUNT", "").strip() or None)

# Админ-токен для веб-UI
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()

# ---- Телеметрия ----
TELEMETRY_ENABLED   = os.getenv("TELEMETRY_ENABLED", "true").lower() in ("1","true","yes","y")
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_THREAD_ID  = os.getenv("TELEGRAM_THREAD_ID", "").strip() or None
APP_NAME            = os.getenv("APP_NAME", "").strip() or os.getenv("HEROKU_APP_NAME", "").strip() or "TradingBot"
ENV_NAME            = os.getenv("ENV", "").strip() or ("heroku" if os.getenv("DYNO") else "local")

# ---- Инициализация SDK (если хотим) ----
sdk_spot: 'SpotApi | None' = None
if USE_SDK and API_KEY and API_SECRET and SpotApi and ApiClient and Configuration:
    try:
        cfg = Configuration(key=API_KEY, secret=API_SECRET)
        cfg.host = HOST
        sdk_spot = SpotApi(ApiClient(cfg))
    except Exception:
        sdk_spot = None
        USE_SDK = False
