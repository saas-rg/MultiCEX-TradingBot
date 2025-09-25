# core/adapters/gate_v4.py
from __future__ import annotations
from decimal import Decimal
from typing import Tuple, List, Dict, Any, Optional

from core.exchange_base import ExchangeAdapter

# Делегируем в существующую реализацию
from exchanges import gate as gate

class GateV4Adapter(ExchangeAdapter):
    def __init__(self, config: Dict[str, Any] | None = None):
        # Если твой exchanges/gate.py требует явной инициализации — можно сделать здесь.
        # Сейчас не трогаем: поведение 1:1.
        self._config = config or {}

    def exchange_name(self) -> str:
        return "gate"

    # meta
    def get_server_time_epoch(self) -> int:
        return int(gate.get_server_time_epoch())

    # rules
    def get_pair_rules(self, pair: str) -> Tuple[int, int, Decimal, Decimal]:
        return gate.get_pair_rules(pair)

    # market data
    def get_last_price(self, pair: str) -> Decimal:
        return gate.get_last_price(pair)

    def get_prev_minute_close(self, pair: str) -> Decimal:
        return gate.get_prev_minute_close(pair)

    # trading / orders
    def place_limit_buy(self, pair: str, price: str, amount: str, account: Optional[str] = None) -> str:
        return gate.place_limit_buy(pair=pair, price=price, amount=amount, account=account)

    def market_sell(self, pair: str, amount_base: str, account: Optional[str] = None) -> str:
        return gate.market_sell(pair=pair, amount_base=amount_base, account=account)

    def cancel_order(self, pair: str, order_id: str) -> None:
        gate.cancel_order(pair=pair, order_id=order_id)

    def cancel_all_open_orders(self, pair: str) -> None:
        gate.cancel_all_open_orders(pair)

    def list_open_orders(self, pair: str) -> List[Dict[str, Any]]:
        return gate.list_open_orders(pair)

    def get_order_detail(self, pair: str, order_id: str) -> Dict[str, Any]:
        return gate.get_order_detail(pair=pair, order_id=order_id)

    # account
    def get_available(self, asset: str) -> Decimal:
        return gate.get_available(asset)
