import time
import requests
from urllib.parse import urlencode
from typing import Dict, Any, Optional
from config import HOST, REQ_TIMEOUT, RETRIES, API_KEY
from .signing import headers_signed

# Глобальная сессия с пулом соединений (keep-alive)
SESSION = requests.Session()
# Поднимем лимиты пула, чтобы параллельные запросы не ждали
ADAPTER = requests.adapters.HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=0)
SESSION.mount("https://", ADAPTER)
SESSION.mount("http://", ADAPTER)

def request(method: str,
            path: str,
            query: Optional[Dict[str, Any]] = None,
            body: Optional[Dict[str, Any]] = None,
            signed: bool = False):
    """
    Унифицированный HTTP-вызов.
    - public: собирает URL как HOST+path+(?query), тело отправляет json
    - signed: использует headers_signed(...) и отправляет body через data (как требует Gate.io)
    """
    url = HOST + path
    for attempt in range(RETRIES + 1):
        try:
            if signed:
                headers, q, b = headers_signed(method, path, query, body)
                headers["KEY"] = API_KEY
                full_url = url if not q else f"{url}?{q}"
                resp = SESSION.request(method, full_url,
                                       data=b if body else None,
                                       headers=headers,
                                       timeout=REQ_TIMEOUT)
            else:
                full_url = url if not query else f"{url}?{urlencode(query, doseq=True)}"
                resp = SESSION.request(method, full_url,
                                       json=body,
                                       timeout=REQ_TIMEOUT)

            if 200 <= resp.status_code < 300:
                txt = resp.text.strip()
                return resp.json() if txt else None

            try:
                info = resp.json()
            except Exception:
                info = resp.text
            raise RuntimeError(f"HTTP {resp.status_code} {method} {path}: {info}")

        except Exception:
            if attempt >= RETRIES:
                raise
            # простой экспоненциальный джиттер
            time.sleep(0.3 * (attempt + 1))
