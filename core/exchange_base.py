# core/exchange_base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Tuple, List, Dict, Any, Optional

class ExchangeAdapter(ABC):
    # meta
    @abstractmethod
    def exchange_name(self) -> str: ...
    @abstractmethod
    def get_server_time_epoch(self) -> int: ...

    # market meta/rules
    @abstractmethod
    def get_pair_rules(self, pair: str) -> Tuple[int, int, Decimal, Decimal]: ...

    # market data
    @abstractmethod
    def get_last_price(self, pair: str) -> Decimal: ...
    @abstractmethod
    def get_prev_minute_close(self, pair: str) -> Decimal: ...

    # trading / orders
    @abstractmethod
    def place_limit_buy(self, pair: str, price: str, amount: str, account: Optional[str] = None) -> str: ...
    @abstractmethod
    def market_sell(self, pair: str, amount_base: str, account: Optional[str] = None) -> str: ...
    @abstractmethod
    def cancel_order(self, pair: str, order_id: str) -> None: ...
    @abstractmethod
    def cancel_all_open_orders(self, pair: str) -> None: ...
    @abstractmethod
    def list_open_orders(self, pair: str) -> List[Dict[str, Any]]: ...
    @abstractmethod
    def get_order_detail(self, pair: str, order_id: str) -> Dict[str, Any]: ...

    # account
    @abstractmethod
    def get_available(self, asset: str) -> Decimal: ...
