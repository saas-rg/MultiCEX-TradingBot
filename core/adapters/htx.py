# core/adapters/htx.py
from __future__ import annotations

import time
import hmac
import hashlib
import base64
import json
import random
import functools
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse, quote

import httpx

from core.exchange_base import ExchangeAdapter
from config import (
    get_exchange_cfg,
    REQ_TIMEOUT as HTTP_TIMEOUT,
    RETRIES,
    APP_NAME,
    ENV_NAME,
)

# === helpers ===

def _to_htx_symbol(pair: str) -> str:
    # "BTC_USDT" -> "btcusdt"
    return pair.replace("_", "").lower()

def _iso_utc_now() -> str:
    # Huobi/HTX Signature V2 uses UTC time in ISO8601 without ms
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

def _is_transient(err: Exception) -> bool:
    s = str(err).lower()
    # простая эвристика по ошибкам сети/таймаутам/429/5xx
    return any(k in s for k in [
        "timeout", "timed out", "connection", "reset", "econn", "read timed",
        "429", " 5", "server error", "temporarily"
    ])

def _retryable(fn):
    @functools.wraps(fn)
    def wrap(*args, **kwargs):
        attempts = max(1, int(RETRIES))
        last = None
        for i in range(attempts):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                if not _is_transient(e) or i == attempts - 1:
                    raise
                last = e
                # 0.35 * 2^i + jitter(0..0.25)
                delay = 0.35 * (2 ** i) + random.uniform(0, 0.25)
                time.sleep(delay)
        # на всякий случай
        raise last
    return wrap


class HTXAdapter(ExchangeAdapter):
    """
    Минимальный адаптер HTX Spot с интерфейсом, совместимым с GateV4Adapter.
    Реализованы методы:
      - get_server_time_epoch()
      - get_pair_rules(pair) -> (price_precision, amount_precision, min_base, min_quote)
      - get_last_price(pair)
      - get_prev_minute_close(pair)
      - place_limit_buy(pair, price, amount, account=None) -> order_id
      - market_sell(pair, amount_base, account=None) -> order_id
      - cancel_order(pair, order_id) -> None
      - cancel_all_open_orders(pair) -> None
      - list_open_orders(pair) -> List[dict]
      - get_order_detail(pair, order_id) -> Dict[str,Any]
      - get_available(asset) -> Decimal
      - fetch_trades(pair, start_ts, end_ts, limit) -> List[dict]
    """

    def __init__(self, _config_ctx: Any):
        cfg = get_exchange_cfg("htx")
        self.api_key: str = (cfg.get("api_key") or "").strip()
        self.api_secret: str = (cfg.get("api_secret") or "").strip()
        self.account_type: str = (cfg.get("account_type") or "spot").strip().lower()

        # Базовый REST-эндпоинт берём из конфигурации (поддержка переопределения через .env)
        host = (cfg.get("host") or "https://api.huobi.pro").rstrip("/")
        self.public_base = host
        self.private_base = host

        # Опциональный SDK (если huobi установлен и use_sdk=true)
        self._use_sdk: bool = bool(cfg.get("use_sdk"))
        self._sdk = cfg.get("sdk") if self._use_sdk else None  # dict: {"market","account","trade"} | None

        # HTTP клиент
        self._http = httpx.Client(timeout=HTTP_TIMEOUT, headers={
            "User-Agent": f"{APP_NAME or 'TradingBot'}/{ENV_NAME or 'local'} (+htx-adapter)"
        })

        self._account_id: Optional[str] = None

        # кеш правил символов: "btcusdt" -> (price_prec, amount_prec, min_base, min_quote)
        self._rules_cache: dict[str, Tuple[int, int, Decimal, Decimal]] = {}

    def exchange_name(self) -> str:
        return "htx"

    # ---- подпись (Signature V2) ----

    def _auth_headers(self) -> Dict[str, str]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("HTX: API key/secret not configured")
        return {"Content-Type": "application/json"}

    def _sign_url(self, method: str, path: str, params: Dict[str, Any]) -> str:
        """
        Подпись запроса (Signature V2).
        Док: https://huobiapi.github.io/docs/spot/v1/en/#api-signature
        """
        method = method.upper()
        ts = _iso_utc_now()

        # базовые параметры подписи
        auth_params = {
            "AccessKeyId": self.api_key,
            "SignatureMethod": "HmacSHA256",
            "SignatureVersion": "2",
            "Timestamp": ts,
        }
        # объединяем с params (query/body, у нас — query)
        all_params = {**params, **auth_params}

        # canonical query
        # Huobi требует percent-encode ключей и значений + сортировку по ключу
        def _pct(s: str) -> str:
            # safe chars per RFC3986
            return quote(str(s), safe='~-._')

        sorted_items = sorted(all_params.items(), key=lambda kv: kv[0])
        canonical_query = "&".join([f"{_pct(k)}={_pct(v)}" for k, v in sorted_items])

        # host
        parsed = urlparse(self.private_base)
        host = parsed.netloc
        # canonical string
        payload = "\n".join([method, host, path, canonical_query]).encode("utf-8")

        # HMAC-SHA256 -> base64
        sign = hmac.new(self.api_secret.encode("utf-8"), payload, hashlib.sha256).digest()
        signature = base64.b64encode(sign).decode("utf-8")

        # итоговый URL
        final_query = canonical_query + "&Signature=" + quote(signature, safe='~-._')
        return f"{self.private_base}{path}?{final_query}"

    # ---- account id / balances ----

    @_retryable
    def _ensure_account_id(self) -> str:
        if self._account_id:
            return self._account_id
        url = self._sign_url("GET", "/v1/account/accounts", {})
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        data = (r.json() or {}).get("data") or []
        # выбираем первый spot с state=working
        for a in data:
            if str(a.get("type", "")).lower() == "spot" and str(a.get("state", "")).lower() == "working":
                self._account_id = str(a.get("id"))
                break
        if not self._account_id:
            # fallback: любой working
            for a in data:
                if str(a.get("state", "")).lower() == "working":
                    self._account_id = str(a.get("id"))
                    break
        if not self._account_id:
            raise RuntimeError("HTX: no working account found")
        return self._account_id

    @_retryable
    def _balances_map(self) -> Dict[str, Decimal]:
        acc_id = self._ensure_account_id()
        url = self._sign_url("GET", f"/v1/account/accounts/{acc_id}/balance", {})
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        lst = ((r.json() or {}).get("data") or {}).get("list") or []
        out: Dict[str, Decimal] = {}
        for it in lst:
            t = str(it.get("type", "")).lower()
            if t not in ("trade", "frozen"):
                continue
            cc = str(it.get("currency", "")).upper()
            bal = Decimal(str(it.get("balance", "0")) or "0")
            out[cc] = out.get(cc, Decimal("0")) + bal
        return out

    # ---- совместимый интерфейс ----

    def get_server_time_epoch(self) -> int:
        # У HTX есть /v1/common/timestamp (ms), но нам достаточно локального времени
        return int(time.time())

    def get_pair_rules(self, pair: str) -> Tuple[int, int, Decimal, Decimal]:
        """
        Возвращает (price_precision, amount_precision, min_base, min_quote)
        """
        sym = _to_htx_symbol(pair)
        if sym in self._rules_cache:
            return self._rules_cache[sym]

        # Публичная справочная ручка
        url = f"{self.public_base}/v1/common/symbols"
        r = self._http.get(url)
        r.raise_for_status()
        arr = (r.json() or {}).get("data") or []
        for it in arr:
            if str(it.get("symbol", "")).lower() == sym:
                price_prec = int(it.get("price-precision", 8))
                amount_prec = int(it.get("amount-precision", 8))
                min_base = Decimal(str(it.get("min-order-amt", "0")) or "0")
                min_quote = Decimal(str(it.get("min-order-value", "0")) or "0")
                self._rules_cache[sym] = (price_prec, amount_prec, min_base, min_quote)
                return self._rules_cache[sym]
        raise RuntimeError(f"HTX: symbol not found {pair}")

    # --- SDK shortcuts (опционально) ---

    def _sdk_get_last_price(self, pair: str) -> Optional[Decimal]:
        """
        Попытка получить последнюю цену через huobi SDK (если доступен).
        Возвращает Decimal или None (если не удалось).
        """
        if not self._sdk:
            return None
        try:
            market = self._sdk.get("market") if isinstance(self._sdk, dict) else None
            if market is None:
                return None
            sym = _to_htx_symbol(pair)
            # get candlesticks or trade tick; чтобы не плодить зависимости — попробуем через market.get_candlestick
            try:
                from huobi.constant import CandlestickInterval  # type: ignore
                kl = market.get_candlestick(sym, CandlestickInterval.MIN1, 1)
                if kl:
                    # последняя свеча ещё может быть текущей; для last price норм
                    px = Decimal(str(kl[0].close))
                    return px
            except Exception:
                pass
            # fallback: last trade
            trades = market.get_trade(sym)
            if trades and trades[0].data:
                px = Decimal(str(trades[0].data[0].price))
                return px
        except Exception:
            return None
        return None

    def _sdk_get_prev_minute_close(self, pair: str) -> Optional[Decimal]:
        """
        Попытка получить закрытие предыдущей 1m свечи через SDK (если доступен).
        Возвращает Decimal или None.
        """
        if not self._sdk:
            return None
        try:
            market = self._sdk.get("market") if isinstance(self._sdk, dict) else None
            if market is None:
                return None
            sym = _to_htx_symbol(pair)
            from huobi.constant import CandlestickInterval  # type: ignore
            kl = market.get_candlestick(sym, CandlestickInterval.MIN1, 2)
            closes: List[Decimal] = []
            for k in kl or []:
                closes.append(Decimal(str(k.close)))
            if len(closes) >= 2:
                # второй с конца — закрытая свеча
                return closes[-2]
        except Exception:
            return None
        return None

    # ---- рыночные данные ----

    @_retryable
    def get_last_price(self, pair: str) -> Decimal:
        px = self._sdk_get_last_price(pair)
        if px is not None:
            return px

        sym = _to_htx_symbol(pair)
        url = f"{self.public_base}/market/trade"
        r = self._http.get(url, params={"symbol": sym})
        r.raise_for_status()
        js = r.json() or {}
        ticks = ((js.get("tick") or {}).get("data") or [])
        if not ticks:
            raise RuntimeError(f"HTX: no trade data for {pair}")
        return Decimal(str(ticks[0].get("price", "0")))

    @_retryable
    def get_prev_minute_close(self, pair: str) -> Decimal:
        px = self._sdk_get_prev_minute_close(pair)
        if px is not None:
            return px

        sym = _to_htx_symbol(pair)
        url = f"{self.public_base}/market/history/kline"
        r = self._http.get(url, params={"symbol": sym, "period": "1min", "size": 2})
        r.raise_for_status()
        arr = (r.json() or {}).get("data") or []
        if len(arr) < 2:
            raise RuntimeError(f"HTX: not enough klines for {pair}")
        # массив в порядке от новой к старой; закрытая — [1]
        return Decimal(str(arr[1].get("close", "0")))

    # ---- торговые методы ----

    @_retryable
    def place_limit_buy(self, pair: str, price: str, amount: str, account: str | None = None) -> str:
        """
        POST /v1/order/orders/place
        type = buy-limit
        """
        acc_id = account or self._ensure_account_id()
        body = {
            "account-id": acc_id,
            "symbol": _to_htx_symbol(pair),
            "type": "buy-limit",
            "price": str(price),
            "amount": str(amount),
            "source": "api",
        }
        url = self._sign_url("POST", "/v1/order/orders/place", {})
        r = self._http.post(url, headers=self._auth_headers(), content=json.dumps(body).encode("utf-8"))
        r.raise_for_status()
        js = r.json() or {}
        if js.get("status") != "ok":
            raise RuntimeError(f"HTX place_limit_buy failed: {js}")
        oid = str(js.get("data", ""))
        if not oid:
            raise RuntimeError(f"HTX place_limit_buy: empty order id: {js}")
        return oid

    @_retryable
    def market_sell(self, pair: str, amount_base: str, account: str | None = None) -> str:
        """
        POST /v1/order/orders/place
        type = sell-market
        """
        acc_id = account or self._ensure_account_id()
        body = {
            "account-id": acc_id,
            "symbol": _to_htx_symbol(pair),
            "type": "sell-market",
            "amount": str(amount_base),
            "source": "api",
        }
        url = self._sign_url("POST", "/v1/order/orders/place", {})
        r = self._http.post(url, headers=self._auth_headers(), content=json.dumps(body).encode("utf-8"))
        r.raise_for_status()
        js = r.json() or {}
        if js.get("status") != "ok":
            raise RuntimeError(f"HTX market_sell failed: {js}")
        oid = str(js.get("data", ""))
        if not oid:
            raise RuntimeError(f"HTX market_sell: empty order id: {js}")
        return oid

    @_retryable
    def cancel_order(self, pair: str, order_id: str) -> None:
        """
        POST /v1/order/orders/{order-id}/submitcancel
        """
        url = self._sign_url("POST", f"/v1/order/orders/{order_id}/submitcancel", {})
        r = self._http.post(url, headers=self._auth_headers(), content=b"{}")
        r.raise_for_status()
        js = r.json() or {}
        if js.get("status") != "ok":
            raise RuntimeError(f"HTX cancel_order failed: {js}")

    @_retryable
    def cancel_all_open_orders(self, pair: str) -> None:
        """
        POST /v1/order/orders/batchCancelOpenOrders  (по символу)
        """
        acc_id = self._ensure_account_id()
        body = {
            "account-id": acc_id,
            "symbol": _to_htx_symbol(pair),
        }
        url = self._sign_url("POST", "/v1/order/orders/batchCancelOpenOrders", {})
        r = self._http.post(url, headers=self._auth_headers(), content=json.dumps(body).encode("utf-8"))
        r.raise_for_status()
        js = r.json() or {}
        if js.get("status") != "ok":
            raise RuntimeError(f"HTX cancel_all_open_orders failed: {js}")

    @_retryable
    def list_open_orders(self, pair: str) -> List[Dict[str, Any]]:
        """
        GET /v1/order/openOrders
        """
        acc_id = self._ensure_account_id()
        params = {
            "account-id": acc_id,
            "symbol": _to_htx_symbol(pair),
        }
        url = self._sign_url("GET", "/v1/order/openOrders", params)
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        arr = (r.json() or {}).get("data") or []
        # упрощённый маппинг, совместимый с нашим отчётчиком
        out: List[Dict[str, Any]] = []
        for it in arr:
            out.append({
                "id": str(it.get("id", "")),
                "price": str(it.get("price", "0")),
                "amount": str(it.get("amount", "0")),
                "field-amount": str(it.get("field-amount", it.get("filled-amount", "0"))),
                "state": str(it.get("state", "")),
                "type": str(it.get("type", "")),
                "created-at": int(it.get("created-at", 0)),
            })
        return out

    @_retryable
    def get_order_detail(self, pair: str, order_id: str) -> Dict[str, Any]:
        """
        GET /v1/order/orders/{order-id}
        """
        url = self._sign_url("GET", f"/v1/order/orders/{order_id}", {})
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        js = r.json() or {}
        if js.get("status", "ok") != "ok":
            raise RuntimeError(f"HTX get_order_detail failed: {js}")
        data = js.get("data") or {}
        # минимальный нормализатор
        return {
            "id": str(data.get("id", "")),
            "symbol": str(data.get("symbol", "")),
            "price": str(data.get("price", "0")),
            "amount": str(data.get("amount", "0")),
            "field-amount": str(data.get("field-amount", data.get("filled-amount", "0"))),
            "state": str(data.get("state", "")),
            "type": str(data.get("type", "")),
            "created-at": int(data.get("created-at", 0)),
            "finished-at": int(data.get("finished-at", 0)),
        }

    # ---- отчёты: свои сделки за интервал ----

    @_retryable
    def fetch_trades(self, pair: str, start_ts: Optional[int] = None, end_ts: Optional[int] = None,
                     limit: Optional[int] = None, **kwargs) -> List[Dict[str, Any]]:
        """
        GET /v1/order/matchresults?symbol=...&start-time=ms&end-time=ms&size=...
        Нормализуем под reporting._norm_trade_row().
        """
        sym = _to_htx_symbol(pair)
        params = {
            "symbol": sym,
            "start-time": int(start_ts or 0) * 1000,
            "end-time": int(end_ts or int(time.time())) * 1000,
            "size": int(limit or 1000),
        }
        url = self._sign_url("GET", "/v1/order/matchresults", params)
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        arr = (r.json() or {}).get("data") or []
        out: List[Dict[str, Any]] = []
        for it in arr:
            out.append({
                "ts": int(it.get("created-at", 0)) // 1000,
                "price": str(it.get("price", "0")),
                "amount": str(it.get("filled-amount", it.get("filled-qty", "0"))),
                "side": str(it.get("type", "").split("-")[0]).lower(),  # buy/sell
                "fee": str(it.get("filled-fees", it.get("fee", "0"))),
                "fee_currency": str(it.get("fee-currency", it.get("fee-currency-type", "USDT"))).upper(),
                "trade_id": str(it.get("id", it.get("trade-id", ""))),
            })
        # стабильная сортировка: по времени, затем по trade_id
        out.sort(key=lambda x: (x["ts"], x.get("trade_id", "")))
        return out

    # ---- балансы ----

    def get_available(self, asset: str) -> Decimal:
        bal = self._balances_map()
        return bal.get(asset.upper(), Decimal("0"))
