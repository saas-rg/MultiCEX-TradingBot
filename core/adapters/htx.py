# core/adapters/htx.py
from __future__ import annotations

import time
import hmac
import hashlib
import base64
import json
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from core.exchange_base import ExchangeAdapter
from config import (
    HTX_API_KEY, HTX_API_SECRET, HTX_ACCOUNT_TYPE,
    REQ_TIMEOUT as HTTP_TIMEOUT,
)

# === helpers ===

def _to_htx_symbol(pair: str) -> str:
    # "BTC_USDT" -> "btcusdt"
    return pair.replace("_", "").lower()

def _iso_utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

# === HTX Spot adapter ===

class HTXAdapter(ExchangeAdapter):
    """
    Минимальный адаптер HTX Spot с интерфейсом, совместимым с GateV4Adapter.
    Реализованы:
      - get_server_time_epoch()
      - get_pair_rules(pair) -> (price_prec, amount_prec, min_base, min_quote)
      - get_last_price(pair)  (по последней сделке)
      - get_prev_minute_close(pair) (закрытие предыдущей 1m свечи)
      - place_limit_buy(pair, price, amount, account=None) -> order_id
      - market_sell(pair, amount_base, account=None) -> order_id
      - cancel_order(pair, order_id) -> None
      - cancel_all_open_orders(pair) -> None
      - list_open_orders(pair) -> List[dict]
      - get_order_detail(pair, order_id) -> Dict[str,Any]
      - get_available(asset) -> Decimal
      - fetch_trades(pair, start_ts, end_ts, limit) -> List[dict]  (для отчётов)
    """

    def __init__(self, config_ctx: Any):
        self.api_key = (HTX_API_KEY or "").strip()
        self.api_secret = (HTX_API_SECRET or "").strip()
        self.account_type = (HTX_ACCOUNT_TYPE or "spot").strip().lower()

        # Официальный REST для spot
        self.public_base = "https://api.huobi.pro"
        self.private_base = "https://api.huobi.pro"

        self._http = httpx.Client(timeout=HTTP_TIMEOUT)
        self._account_id: Optional[str] = None

    def exchange_name(self) -> str:
        return "htx"

    # ---- подпись (Signature V2) ----

    def _auth_headers(self) -> Dict[str, str]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("HTX: API key/secret not configured")
        return {"Content-Type": "application/json"}

    def _sign_url(self, method: str, path: str, extra_params: Dict[str, Any]) -> str:
        host = self.private_base.replace("https://", "")
        params = {
            "AccessKeyId": self.api_key,
            "SignatureMethod": "HmacSHA256",
            "SignatureVersion": "2",
            "Timestamp": _iso_utc_now(),
        }
        params.update({k: v for k, v in (extra_params or {}).items() if v is not None})
        sorted_items = sorted(params.items(), key=lambda kv: kv[0])
        qs = urlencode(sorted_items)
        canonical = f"{method}\n{host}\n{path}\n{qs}"
        digest = hmac.new(self.api_secret.encode(), canonical.encode(), hashlib.sha256).digest()
        params["Signature"] = base64.b64encode(digest).decode()
        return f"{self.private_base}{path}?{urlencode(params)}"

    # ---- account id / balances ----

    def _ensure_account_id(self) -> str:
        if self._account_id:
            return self._account_id
        url = self._sign_url("GET", "/v1/account/accounts", {})
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        data = (r.json() or {}).get("data") or []
        # выбираем первый spot с state=working
        for a in data:
            if str(a.get("type","")).lower() == "spot" and str(a.get("state","")).lower() == "working":
                self._account_id = str(a.get("id"))
                break
        if not self._account_id:
            # fallback: любой working
            for a in data:
                if str(a.get("state","")).lower() == "working":
                    self._account_id = str(a.get("id"))
                    break
        if not self._account_id:
            raise RuntimeError("HTX: no working account found")
        return self._account_id

    def _balances_map(self) -> Dict[str, Decimal]:
        acc_id = self._ensure_account_id()
        url = self._sign_url("GET", f"/v1/account/accounts/{acc_id}/balance", {})
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        lst = ((r.json() or {}).get("data") or {}).get("list") or []
        out: Dict[str, Decimal] = {}
        for it in lst:
            t = str(it.get("type",""))
            if t not in ("trade","frozen"):
                continue
            cc = str(it.get("currency","")).upper()
            bal = Decimal(str(it.get("balance","0")) or "0")
            out[cc] = out.get(cc, Decimal("0")) + bal
        return out

    # ---- совместимый интерфейс ----

    def get_server_time_epoch(self) -> int:
        # HTX имеет /v1/common/timestamp (ms). Можно не вызывать — достаточно локального времени.
        return int(time.time())

    def get_pair_rules(self, pair: str) -> Tuple[int, int, Decimal, Decimal]:
        """
        Возвращает (price_precision, amount_precision, min_base, min_quote)
        """
        sym = _to_htx_symbol(pair)
        url = f"{self.public_base}/v1/common/symbols"
        r = self._http.get(url)
        r.raise_for_status()
        arr = (r.json() or {}).get("data") or []
        for it in arr:
            if str(it.get("symbol","")).lower() == sym:
                price_prec  = int(it.get("price-precision", 8))
                amount_prec = int(it.get("amount-precision", 8))
                min_base = Decimal(str(it.get("min-order-amt","0")) or "0")
                min_quote = Decimal(str(it.get("min-order-value","0")) or "0")
                return price_prec, amount_prec, min_base, min_quote
        raise RuntimeError(f"HTX: symbol not found {pair}")

    def get_last_price(self, pair: str) -> Decimal:
        """
        Последняя цена по последней сделке:
        GET /market/trade?symbol=btcusdt
        """
        sym = _to_htx_symbol(pair)
        url = f"{self.public_base}/market/trade"
        r = self._http.get(url, params={"symbol": sym})
        r.raise_for_status()
        js = r.json() or {}
        ticks = ((js.get("tick") or {}).get("data") or [])
        if not ticks:
            raise RuntimeError(f"HTX: no trade data for {pair}")
        return Decimal(str(ticks[0].get("price", "0")))

    def get_prev_minute_close(self, pair: str) -> Decimal:
        """
        Закрытие ПРЕДЫДУЩЕЙ 1-мин свечи:
        GET /market/history/kline?symbol=btcusdt&period=1min&size=2
        """
        sym = _to_htx_symbol(pair)
        url = f"{self.public_base}/market/history/kline"
        r = self._http.get(url, params={"symbol": sym, "period": "1min", "size": 2})
        r.raise_for_status()
        data = (r.json() or {}).get("data") or []
        if len(data) < 2:
            raise RuntimeError(f"HTX: not enough klines for {pair}")
        # data[0] — последняя закрытая, data[1] — предыдущая закрытая (по документации/порядку)
        prev = data[1]
        return Decimal(str(prev.get("close", "0")))

    def place_limit_buy(self, pair: str, price: str, amount: str, account: str | None = None) -> str:
        """
        POST /v1/order/orders/place
        type=buy-limit
        """
        acc_id = self._ensure_account_id()
        body = {
            "account-id": acc_id,
            "symbol": _to_htx_symbol(pair),
            "type": "buy-limit",
            "price": str(price),
            "amount": str(amount),
        }
        url = self._sign_url("POST", "/v1/order/orders/place", {})
        r = self._http.post(url, headers=self._auth_headers(), content=json.dumps(body, separators=(",", ":")))
        r.raise_for_status()
        js = r.json() or {}
        if js.get("status") != "ok":
            raise RuntimeError(f"HTX place_limit_buy failed: {js}")
        oid = str(js.get("data",""))
        if not oid:
            raise RuntimeError(f"HTX place_limit_buy: empty order id: {js}")
        return oid

    def market_sell(self, pair: str, amount_base: str, account: str | None = None) -> str:
        """
        POST /v1/order/orders/place
        type=sell-market
        """
        acc_id = self._ensure_account_id()
        body = {
            "account-id": acc_id,
            "symbol": _to_htx_symbol(pair),
            "type": "sell-market",
            "amount": str(amount_base),
        }
        url = self._sign_url("POST", "/v1/order/orders/place", {})
        r = self._http.post(url, headers=self._auth_headers(), content=json.dumps(body, separators=(",", ":")))
        r.raise_for_status()
        js = r.json() or {}
        if js.get("status") != "ok":
            raise RuntimeError(f"HTX market_sell failed: {js}")
        oid = str(js.get("data",""))
        if not oid:
            raise RuntimeError(f"HTX market_sell: empty order id: {js}")
        return oid

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
        r = self._http.post(url, headers=self._auth_headers(), content=json.dumps(body, separators=(",", ":")))
        r.raise_for_status()
        js = r.json() or {}
        if js.get("status") != "ok":
            raise RuntimeError(f"HTX cancel_all_open_orders failed: {js}")

    def list_open_orders(self, pair: str) -> List[Dict[str, Any]]:
        """
        GET /v1/order/openOrders?account-id=...&symbol=...
        """
        acc_id = self._ensure_account_id()
        params = {"account-id": acc_id, "symbol": _to_htx_symbol(pair)}
        url = self._sign_url("GET", "/v1/order/openOrders", params)
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        arr = (r.json() or {}).get("data") or []
        # Нормализуем несколько ключей под наш общий вид
        out: List[Dict[str, Any]] = []
        for it in arr:
            out.append({
                "id": str(it.get("id","")),
                "status": str(it.get("state","")),
                "price": str(it.get("price","0")),
                "amount": str(it.get("amount","0")),
                "filled": str(it.get("filled-amount", it.get("field-amount","0"))),
                "type": str(it.get("type","")),  # buy-limit/sell-limit/...
                "create_time": int(it.get("created-at", 0)) // 1000,
            })
        return out

    def get_order_detail(self, pair: str, order_id: str) -> Dict[str, Any]:
        """
        GET /v1/order/orders/{order-id}
        """
        url = self._sign_url("GET", f"/v1/order/orders/{order_id}", {})
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        data = (r.json() or {}).get("data") or {}
        # Немного унифицируем поля
        return {
            "id": str(data.get("id","")),
            "status": str(data.get("state","")),
            "price": str(data.get("price","0")),
            "amount": str(data.get("amount","0")),
            "filled": str(data.get("field-amount", data.get("filled-amount","0"))),
            "type": str(data.get("type","")),
            "create_time": int(data.get("created-at", 0)) // 1000,
            "update_time": int(data.get("finished-at", 0)) // 1000,
        }

    def get_available(self, asset: str) -> Decimal:
        """
        Вернёт доступный баланс валюты (trade).
        """
        acc_id = self._ensure_account_id()
        url = self._sign_url("GET", f"/v1/account/accounts/{acc_id}/balance", {})
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        lst = ((r.json() or {}).get("data") or {}).get("list") or []
        asset = asset.upper()
        free = Decimal("0")
        for it in lst:
            if str(it.get("currency","")).upper() == asset and str(it.get("type","")) == "trade":
                free += Decimal(str(it.get("balance","0")) or "0")
        return free

    # ---- отчёты: свои сделки за интервал ----

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
                "price": str(it.get("price","0")),
                "amount": str(it.get("filled-amount", it.get("filled-qty","0"))),
                "side": str(it.get("type","").split("-")[0]).lower(),  # buy/sell
                "fee": str(it.get("filled-fees", it.get("fee","0"))),
                "fee_currency": str(it.get("fee-currency", it.get("fee-currency-type","USDT"))).upper(),
                "trade_id": str(it.get("id", it.get("trade-id",""))),
            })
        out.sort(key=lambda x: (x["ts"], x.get("trade_id","")))
        return out
