# core/adapters/htx.py
from __future__ import annotations

"""
HTX Spot Adapter (skeleton for v0.8.0 M6)
-----------------------------------------
Каркас адаптера HTX, совместимый по сигнатурам с текущим движком (как GateV4Adapter).
Все методы пока NotImplementedError — заполним в М6.

Ожидаемая интеграция:
- Реестр подключает класс через core.exchange_proxy._register_defaults_once()
- Объект создаётся лениво: exchange_proxy.get_adapter('htx')
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

try:
    # Если у вас есть базовый интерфейс — наследуемся (необязательно, но удобно для стат-торможения)
    from core.exchange_base import ExchangeAdapter  # type: ignore
except Exception:
    class ExchangeAdapter:  # минимальная заглушка для type checkers
        pass


class HTXAdapter(ExchangeAdapter):  # type: ignore[misc]
    name: str = "htx"

    def __init__(self, config: Any):
        """
        :param config: модуль config (корневой), как в GateV4Adapter
        """
        self.config = config
        # Здесь позже: инициализация клиента HTX (sandbox/prod), ключи, хосты, таймауты и т.п.

    # --- Системные/вспомогательные ---

    def get_server_time_epoch(self) -> int:
        raise NotImplementedError("HTXAdapter.get_server_time_epoch(): implement in M6")

    # --- Правила и метаданные символов ---

    def get_pair_rules(self, pair: str) -> Tuple[int, int, Decimal, Decimal]:
        """
        Возвращает кортеж:
          (pprec, aprec, min_base, min_quote)
        pprec — точность цены (кол-во знаков после запятой),
        aprec — точность количества,
        min_base — минимальное количество base,
        min_quote — минимальный notional в quote.
        """
        raise NotImplementedError("HTXAdapter.get_pair_rules(): implement in M6")

    # --- Цены/свечи ---

    def get_last_price(self, pair: str) -> Decimal:
        raise NotImplementedError("HTXAdapter.get_last_price(): implement in M6")

    def get_prev_minute_close(self, pair: str) -> Decimal:
        """
        Закрытие предыдущей 1-минутной свечи (PRICE_SOURCE=close_1m).
        """
        raise NotImplementedError("HTXAdapter.get_prev_minute_close(): implement in M6")

    # --- Ордеры ---

    def place_limit_buy(self, pair: str, price: str, amount: str, account: Optional[str] = None) -> str:
        """
        Возвращает order_id.
        """
        raise NotImplementedError("HTXAdapter.place_limit_buy(): implement in M6")

    def market_sell(self, pair: str, amount_base: str, account: Optional[str] = None) -> str:
        """
        Рынок SELL (FOK/IOC логика будет выше по слою — drain_base_position делает серию вызовов).
        Возвращает order_id.
        """
        raise NotImplementedError("HTXAdapter.market_sell(): implement in M6")

    def cancel_order(self, pair: str, order_id: str) -> None:
        raise NotImplementedError("HTXAdapter.cancel_order(): implement in M6")

    def cancel_all_open_orders(self, pair: str) -> None:
        raise NotImplementedError("HTXAdapter.cancel_all_open_orders(): implement in M6")

    # --- Чтение ордеров/балансы ---

    def list_open_orders(self, pair: str) -> List[Dict[str, Any]]:
        raise NotImplementedError("HTXAdapter.list_open_orders(): implement in M6")

    def get_order_detail(self, pair: str, order_id: str) -> Dict[str, Any]:
        raise NotImplementedError("HTXAdapter.get_order_detail(): implement in M6")

    def get_available(self, asset: str) -> Decimal:
        raise NotImplementedError("HTXAdapter.get_available(): implement in M6")

    # --- История сделок (для отчётов) ---

    def fetch_trades(
        self,
        pair: str,
        *,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Унифицированный доступ к истории сделок. Возврат — список словарей единого формата,
        совместимый с текущим отчётным пайплайном.
        """
        raise NotImplementedError("HTXAdapter.fetch_trades(): implement in M6")
