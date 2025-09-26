# core/exchange_proxy.py
from __future__ import annotations
from decimal import Decimal
from typing import Tuple, List, Dict, Any, Optional
from core.exchange_base import ExchangeAdapter
from core.adapters.gate_v4 import GateV4Adapter

_adapter: Optional[ExchangeAdapter] = None

def init_adapter(config: Any) -> None:
    """
    Инициализация адаптера. Если config не передан, подхватываем core.config.
    Это избавляет от необходимости импортировать config в runner.py.
    """
    global _adapter
    _adapter = GateV4Adapter(config)

def _require() -> ExchangeAdapter:
    if _adapter is None:
        raise RuntimeError("Exchange adapter is not initialized. Call exchange_proxy.init_adapter() first.")
    return _adapter

# ====== Прокси-функции с теми же сигнатурами, что и были в exchanges/gate.py ======

def get_server_time_epoch() -> int:
    return _require().get_server_time_epoch()

def get_pair_rules(pair: str) -> Tuple[int, int, Decimal, Decimal]:
    return _require().get_pair_rules(pair)

def get_last_price(pair: str) -> Decimal:
    return _require().get_last_price(pair)

def get_prev_minute_close(pair: str) -> Decimal:
    return _require().get_prev_minute_close(pair)

def place_limit_buy(pair: str, price: str, amount: str, account: str | None = None) -> str:
    return _require().place_limit_buy(pair, price, amount, account)

def market_sell(pair: str, amount_base: str, account: str | None = None) -> str:
    return _require().market_sell(pair, amount_base, account)

def cancel_order(pair: str, order_id: str) -> None:
    return _require().cancel_order(pair, order_id)

def cancel_all_open_orders(pair: str) -> None:
    return _require().cancel_all_open_orders(pair)

def list_open_orders(pair: str) -> List[Dict[str, Any]]:
    return _require().list_open_orders(pair)

def get_order_detail(pair: str, order_id: str) -> Dict[str, Any]:
    return _require().get_order_detail(pair, order_id)

def get_available(asset: str) -> Decimal:
    return _require().get_available(asset)

from typing import Any, Optional, List, Dict

def fetch_trades(pair: str, *, exchange: Optional[str] = None,
                 start_ts: Optional[int] = None, end_ts: Optional[int] = None,
                 limit: Optional[int] = None, **kwargs) -> List[Dict[str, Any]]:
    """
    Унифицированный доступ к истории сделок для отчётности.
    exchange пока игнорируем (у нас один активный адаптер Gate),
    но оставляем параметр для будущей мультибиржевости.
    """
    # Пока просто делегируем в текущий активный адаптер
    return _require().fetch_trades(pair=pair, start_ts=start_ts, end_ts=end_ts, limit=limit, **kwargs)
