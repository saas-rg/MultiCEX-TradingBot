from decimal import Decimal
from typing import Tuple, List, Dict, Any

from config import sdk_spot, ACCOUNT_TYPE
from core.http import request as http
from core.quant import dquant, fmt

def get_server_time_epoch() -> int:
    data = http("GET", "/spot/time")
    ms = int(data["server_time"])
    return ms // 1000

def get_pair_rules(pair: str) -> Tuple[int, int, Decimal, Decimal]:
    try:
        if sdk_spot:
            cp = sdk_spot.get_currency_pair(pair)
            price_prec = int(getattr(cp, "price_precision", getattr(cp, "precision", 8)))
            amount_prec = int(getattr(cp, "amount_precision", 0))
            min_base = Decimal(str(getattr(cp, "min_base_amount", "0")))
            min_quote = Decimal(str(getattr(cp, "min_quote_amount", "0")))
            return price_prec, amount_prec, min_base, min_quote
    except Exception:
        pass

    data = http("GET", f"/spot/currency_pairs/{pair}")
    price_prec = int(data.get("price_precision", data.get("precision", 8)))
    amount_prec = int(data.get("amount_precision", 0))
    min_base = Decimal(str(data.get("min_base_amount", "0")))
    min_quote = Decimal(str(data.get("min_quote_amount", "0")))
    return price_prec, amount_prec, min_base, min_quote

def get_last_price(pair: str) -> Decimal:
    try:
        if sdk_spot:
            arr = sdk_spot.list_tickers(currency_pair=pair)
            t = arr[0] if isinstance(arr, list) else arr
            return Decimal(str(getattr(t, "last")))
    except Exception:
        pass

    data = http("GET", "/spot/tickers", {"currency_pair": pair})
    if not data:
        raise RuntimeError("Empty /spot/tickers response")
    return Decimal(str(data[0]["last"]))

def get_prev_minute_close(pair: str) -> Decimal:
    data = http("GET", "/spot/candlesticks", {"currency_pair": pair, "interval": "1m", "limit": 2})
    if not data or len(data) < 2:
        raise RuntimeError("Not enough candlesticks for prev minute close")

    prev = data[-2]
    if isinstance(prev, dict):
        cand_close = prev.get("close") or prev.get("c")
        if cand_close is None:
            raise RuntimeError(f"Unexpected candlestick dict format: {prev}")
        return Decimal(str(cand_close))

    candidates_idx = [2, 5, 1, 3, 4]
    for i in candidates_idx:
        try:
            x = Decimal(str(prev[i]))
            if x > 0:
                return x
        except Exception:
            continue
    raise RuntimeError(f"Unexpected candlestick list format: {prev}")

def list_spot_accounts(currency: str | None = None) -> List[Dict[str, Any]]:
    try:
        if sdk_spot:
            arr = sdk_spot.list_spot_accounts(currency=currency) if currency else sdk_spot.list_spot_accounts()
            out = []
            for a in arr:
                out.append({
                    "currency": getattr(a, "currency"),
                    "available": getattr(a, "available"),
                    "locked": getattr(a, "locked", "0"),
                })
            return out
    except Exception:
        pass

    q = {"currency": currency} if currency else None
    return http("GET", "/spot/accounts", q, None, signed=True) or []

def get_available(currency: str) -> Decimal:
    accs = list_spot_accounts(currency)
    for a in accs:
        if a.get("currency") == currency:
            return Decimal(str(a.get("available", "0")))
    return Decimal(0)

def place_limit_buy(pair: str, price: str, amount: str, account: str | None = ACCOUNT_TYPE) -> str:
    if sdk_spot:
        try:
            import gate_api
            order = gate_api.Order(
                currency_pair=pair,
                type="limit",
                side="buy",
                price=price,
                amount=amount,
                time_in_force="gtc",
                account=account
            )
            res = sdk_spot.create_order(order)
            oid = str(getattr(res, "id", None) or getattr(res, "order_id", None) or "")
            if oid:
                return oid
        except Exception:
            pass
    body = {
        "currency_pair": pair,
        "type": "limit",
        "side": "buy",
        "price": price,
        "amount": amount,
        "time_in_force": "gtc",
    }
    if account:
        body["account"] = account
    res = http("POST", "/spot/orders", None, body, signed=True)
    return str(res.get("id") or res.get("order_id") or "")

def market_sell(pair: str, amount_base: str, account: str | None = ACCOUNT_TYPE) -> str:
    if sdk_spot:
        try:
            import gate_api
            order = gate_api.Order(
                currency_pair=pair,
                type="market",
                side="sell",
                amount=amount_base,
                time_in_force="ioc",
                account=account
            )
            res = sdk_spot.create_order(order)
            oid = str(getattr(res, "id", None) or getattr(res, "order_id", None) or "")
            if oid:
                return oid
        except Exception:
            pass
    body = {
        "currency_pair": pair,
        "type": "market",
        "side": "sell",
        "amount": amount_base,
        "time_in_force": "ioc",
    }
    if account:
        body["account"] = account
    res = http("POST", "/spot/orders", None, body, signed=True)
    return str(res.get("id") or res.get("order_id") or "")

def cancel_order(pair: str, order_id: str):
    if sdk_spot:
        try:
            sdk_spot.cancel_order(order_id, currency_pair=pair)
            return
        except Exception:
            pass
    http("DELETE", f"/spot/orders/{order_id}", {"currency_pair": pair}, None, signed=True)

def cancel_all_open_orders(pair: str):
    http("DELETE", "/spot/orders", {"currency_pair": pair}, None, signed=True)

def get_order_detail(pair: str, order_id: str) -> Dict[str, Any]:
    if sdk_spot:
        try:
            od = sdk_spot.get_order(order_id, currency_pair=pair)
            return {
                "amount": getattr(od, "amount", None),
                "left": getattr(od, "left", None),
                "filled_amount": getattr(od, "filled_amount", None),
                "filled_total": getattr(od, "filled_total", None),
                "avg_deal_price": getattr(od, "avg_deal_price", None),
                "status": getattr(od, "status", None),
            }
        except Exception:
            pass
    return http("GET", f"/spot/orders/{order_id}", {"currency_pair": pair}, None, signed=True)

def list_my_trades(pair: str, limit: int = 200, since_ts: int = 0) -> List[Dict[str, Any]]:
    """
    Последние пользовательские трейды по паре. Допфильтрация по времени — на клиенте.
    """
    params = {"currency_pair": pair, "limit": limit}
    try:
        data = http("GET", "/spot/my_trades", params, None, signed=True) or []
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for t in data:
        ts = int(t.get("create_time", t.get("time", t.get("ts", 0)) or 0))
        if ts > 10_000_000_000:
            ts //= 1000
        if since_ts and ts < since_ts:
            continue
        out.append({
            "id": str(t.get("id") or t.get("trade_id") or t.get("tid") or ""),
            "side": str(t.get("side", "")).lower(),
            "price": str(t.get("price", "0")),
            "amount": str(t.get("amount", "0")),
            "fee": str(t.get("fee", "0")),
            "fee_currency": str(t.get("fee_currency", "USDT")),
            "create_time": ts,
        })
    return out

def list_open_orders(pair: str) -> List[Dict[str, Any]]:
    """
    Возвращает открытые (неисполненные/неотменённые) ордера по паре.
    Минимальный состав: id, left, amount, status, price, side, type.
    """
    try:
        if sdk_spot:
            arr = sdk_spot.list_orders(currency_pair=pair, status="open")
            out: List[Dict[str, Any]] = []
            for o in arr:
                out.append({
                    "id": str(getattr(o, "id", "")),
                    "left": str(getattr(o, "left", "0")),
                    "amount": str(getattr(o, "amount", "0")),
                    "status": str(getattr(o, "status", "")),
                    "price": str(getattr(o, "price", "")),
                    "side": str(getattr(o, "side", "")),
                    "type": str(getattr(o, "type", "")),
                })
            return out
    except Exception:
        pass
    # REST
    data = http("GET", "/spot/open_orders", {"currency_pair": pair}, None, signed=True) or []
    out: List[Dict[str, Any]] = []
    for o in data:
        out.append({
            "id": str(o.get("id") or o.get("order_id") or ""),
            "left": str(o.get("left", "0")),
            "amount": str(o.get("amount", "0")),
            "status": str(o.get("status", "")),
            "price": str(o.get("price", "")),
            "side": str(o.get("side", "")),
            "type": str(o.get("type", "")),
        })
    return out
