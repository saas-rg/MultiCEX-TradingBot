# core/exchanges/htx.py
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

from config import (
    HTX_API_KEY,
    HTX_API_SECRET,
    HTX_ACCOUNT_TYPE,   # "spot" по умолчанию
)

# ---- Вспомогательные утилиты ----

def _to_htx_symbol(pair: str) -> str:
    # Наши пары: BTC_USDT -> htx: btcusdt
    return pair.replace("_", "").lower()

def _now_ms() -> int:
    return int(time.time() * 1000)

def _now_s() -> int:
    return int(time.time())

def _iso_utc_now() -> str:
    # 2025-09-26T18:00:00
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

# ---- Адаптер HTX Spot ----

class HTXAdapter:
    """
    Минимальный стабильный интерфейс HTX Spot (как у Gate).
    Публичные вызовы без ключей, приватные — с подписью (Signature V2).
    """

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, account_type: Optional[str] = None, timeout_sec: float = 10.0):
        self.api_key = (api_key or HTX_API_KEY or "").strip()
        self.api_secret = (api_secret or HTX_API_SECRET or "").strip()
        self.account_type = (account_type or HTX_ACCOUNT_TYPE or "spot").strip().lower()

        # Базовые URL (официальный спот REST)
        self.public_base = "https://api.huobi.pro"
        self.private_base = "https://api.huobi.pro"

        self._http = httpx.Client(timeout=timeout_sec)
        self._account_id: Optional[str] = None  # лениво подтянем

    # ========== Подпись (Signature V2) ==========

    def _auth_headers(self) -> Dict[str, str]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("HTX: API key/secret not configured")
        return {"Content-Type": "application/json"}

    def _sign_query(self, method: str, path: str, extra_params: Dict[str, Any]) -> str:
        """
        method: 'GET'|'POST'
        Возвращает полный URL с подписанным query.
        """
        if not self.api_key or not self.api_secret:
            raise RuntimeError("HTX: API key/secret not configured")

        host = self.private_base.replace("https://", "")
        base_params = {
            "AccessKeyId": self.api_key,
            "SignatureMethod": "HmacSHA256",
            "SignatureVersion": "2",
            "Timestamp": _iso_utc_now(),
        }
        params = {**base_params, **{k: v for k, v in extra_params.items() if v is not None}}
        sorted_items = sorted(params.items(), key=lambda kv: kv[0])
        qs = urlencode(sorted_items)
        canonical = f"{method}\n{host}\n{path}\n{qs}"
        digest = hmac.new(self.api_secret.encode(), canonical.encode(), hashlib.sha256).digest()
        sign = base64.b64encode(digest).decode()
        params["Signature"] = sign
        url = f"{self.private_base}{path}?{urlencode(params)}"
        return url

    # ========== Accounts / Balances ==========

    def _ensure_account_id(self) -> str:
        if self._account_id:
            return self._account_id
        # GET /v1/account/accounts
        url = self._sign_query("GET", "/v1/account/accounts", {})
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        js = r.json()
        data = (js or {}).get("data") or []
        # Берём первый рабочий spot-аккаунт
        for a in data:
            if str(a.get("type", "")).lower() == "spot" and str(a.get("state", "")).lower() == "working":
                self._account_id = str(a["id"])
                break
        if not self._account_id and data:
            # fallback: первый рабочий
            for a in data:
                if str(a.get("state", "")).lower() == "working":
                    self._account_id = str(a["id"])
                    break
        if not self._account_id:
            raise RuntimeError("HTX: no working account found")
        return self._account_id

    def get_balances(self) -> Dict[str, Decimal]:
        """
        GET /v1/account/accounts/{id}/balance
        Возвращает сводный словарь валют с суммой (trade+frozen).
        """
        acc_id = self._ensure_account_id()
        path = f"/v1/account/accounts/{acc_id}/balance"
        url = self._sign_query("GET", path, {})
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        js = r.json()
        data = (js or {}).get("data") or {}
        lst = data.get("list") or []
        out: Dict[str, Decimal] = {}
        for it in lst:
            if str(it.get("type","")) not in ("trade","frozen"):
                continue
            cc = str(it.get("currency","")).upper()
            bal = Decimal(str(it.get("balance","0")) or "0")
            out[cc] = out.get(cc, Decimal("0")) + bal
        return out

    # ========== Market Data ==========

    def get_last_close_1m(self, pair: str) -> Decimal:
        """
        GET /market/history/kline?symbol=btcusdt&period=1min&size=1
        Возвращает close последней закрытой 1-мин свечи.
        """
        sym = _to_htx_symbol(pair)
        url = f"{self.public_base}/market/history/kline"
        params = {"symbol": sym, "period": "1min", "size": 1}
        r = self._http.get(url, params=params)
        r.raise_for_status()
        js = r.json()
        data = (js or {}).get("data") or []
        if not data:
            raise RuntimeError(f"HTX: no kline data for {pair}")
        close = Decimal(str(data[0]["close"]))
        return close

    def get_symbol_info(self, pair: str) -> Dict[str, Any]:
        """
        GET /v1/common/symbols, фильтр по symbol
        Возвращает единый формат: base/quote/precision/mins
        """
        sym = _to_htx_symbol(pair)
        url = f"{self.public_base}/v1/common/symbols"
        r = self._http.get(url)
        r.raise_for_status()
        js = r.json()
        arr = (js or {}).get("data") or []
        for it in arr:
            if str(it.get("symbol","")).lower() == sym:
                return {
                    "base": str(it.get("base-currency","")).upper(),
                    "quote": str(it.get("quote-currency","")).upper(),
                    "price_precision": int(it.get("price-precision", 8)),
                    "amount_precision": int(it.get("amount-precision", 8)),
                    "min_amount": Decimal(str(it.get("min-order-amt","0")) or "0"),
                    "min_value":  Decimal(str(it.get("min-order-value","0")) or "0"),
                }
        raise RuntimeError(f"HTX: symbol not found {pair}")

    # ========== Orders ==========

    def place_limit_buy(self, pair: str, price: Decimal, amount: Decimal) -> str:
        """
        POST /v1/order/orders/place
        body: { 'account-id':..., 'symbol':'btcusdt', 'type':'buy-limit', 'price':'..', 'amount':'..' }
        returns order_id (str)
        """
        acc_id = self._ensure_account_id()
        sym = _to_htx_symbol(pair)
        body = {
            "account-id": acc_id,
            "symbol": sym,
            "type": "buy-limit",
            "price": str(price),
            "amount": str(amount),
        }
        url = self._sign_query("POST", "/v1/order/orders/place", {})
        r = self._http.post(url, headers=self._auth_headers(), content=json.dumps(body, separators=(",", ":")))
        r.raise_for_status()
        js = r.json()
        if (js or {}).get("status") != "ok":
            raise RuntimeError(f"HTX place order failed: {js}")
        order_id = str((js or {}).get("data", ""))
        if not order_id:
            raise RuntimeError(f"HTX place order: empty order id: {js}")
        return order_id

    def cancel_all(self, pair: str) -> int:
        """
        POST /v1/order/orders/batchCancelOpenOrders
        body: {'account-id':..., 'symbol':'btcusdt'}
        returns: количество отменённых (best-effort; читаем из 'data' счетчики)
        """
        acc_id = self._ensure_account_id()
        sym = _to_htx_symbol(pair)
        body = {
            "account-id": acc_id,
            "symbol": sym,
        }
        url = self._sign_query("POST", "/v1/order/orders/batchCancelOpenOrders", {})
        r = self._http.post(url, headers=self._auth_headers(), content=json.dumps(body, separators=(",", ":")))
        r.raise_for_status()
        js = r.json()
        # Формат ответа содержит поля 'success-count'/'failed-count' и списки — у разных ревизий по-разному.
        data = (js or {}).get("data") or {}
        succ = int(data.get("success-count", 0)) if isinstance(data, dict) else 0
        return succ

    def _order_matchresults(self, order_id: str) -> List[Dict[str, Any]]:
        """
        GET /v1/order/orders/{order-id}/matchresults
        Нормализуем формат под reporting._norm_trade_row()
        """
        path = f"/v1/order/orders/{order_id}/matchresults"
        url = self._sign_query("GET", path, {})
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        js = r.json()
        arr = (js or {}).get("data") or []
        out: List[Dict[str, Any]] = []
        for it in arr:
            # Объединяем поля в унифицированный вид
            out.append({
                "ts": int(it.get("created-at", 0)) // 1000,   # ms -> s
                "price": str(it.get("price", "0")),
                "amount": str(it.get("filled-amount", it.get("filled-qty", "0"))),
                "side": str(it.get("type","").split("-")[0]).lower(),  # 'buy-limit'/'sell-market' -> 'buy'/'sell'
                "fee": str(it.get("filled-fees", it.get("fee", "0"))),
                "fee_currency": str(it.get("fee-currency", it.get("fee-currency-type","USDT"))).upper(),
                "trade_id": str(it.get("id", it.get("trade-id",""))),
            })
        return out

    def market_sell_ioc(self, pair: str, amount: Decimal) -> List[Dict[str, Any]]:
        """
        POST /v1/order/orders/place с type='sell-market'
        Возвращаем matсhresults по созданному ордеру (список сделок).
        """
        acc_id = self._ensure_account_id()
        sym = _to_htx_symbol(pair)
        body = {
            "account-id": acc_id,
            "symbol": sym,
            "type": "sell-market",
            "amount": str(amount),   # у спота amount в BASE
        }
        url = self._sign_query("POST", "/v1/order/orders/place", {})
        r = self._http.post(url, headers=self._auth_headers(), content=json.dumps(body, separators=(",", ":")))
        r.raise_for_status()
        js = r.json()
        if (js or {}).get("status") != "ok":
            raise RuntimeError(f"HTX market sell failed: {js}")
        order_id = str((js or {}).get("data", ""))
        if not order_id:
            return []
        # Получим матчи и вернём нормализованный список
        return self._order_matchresults(order_id)

    # ========== Trades (for reporting) ==========

    def fetch_trades(self, pair: str, start_ts: int, end_ts: int, limit: int = 1000) -> List[Dict[str, Any]]:
        """
        GET /v1/order/matchresults?symbol=...&start-time=...&end-time=...&size=...
        Возвращает нормализованные записи (см. reporting._norm_trade_row).
        """
        sym = _to_htx_symbol(pair)
        params = {
            "symbol": sym,
            "start-time": int(start_ts) * 1000,  # ms
            "end-time": int(end_ts) * 1000,      # ms
            "size": int(limit),
        }
        url = self._sign_query("GET", "/v1/order/matchresults", params)
        r = self._http.get(url, headers=self._auth_headers())
        r.raise_for_status()
        js = r.json()
        arr = (js or {}).get("data") or []
        out: List[Dict[str, Any]] = []
        for it in arr:
            out.append({
                "ts": int(it.get("created-at", 0)) // 1000,
                "price": str(it.get("price", "0")),
                "amount": str(it.get("filled-amount", it.get("filled-qty", "0"))),
                "side": str(it.get("type","").split("-")[0]).lower(),
                "fee": str(it.get("filled-fees", it.get("fee", "0"))),
                "fee_currency": str(it.get("fee-currency", it.get("fee-currency-type","USDT"))).upper(),
                "trade_id": str(it.get("id", it.get("trade-id",""))),
            })
        # Сортировка по времени/ид для стабильности
        out.sort(key=lambda x: (x["ts"], x.get("trade_id","")))
        return out
