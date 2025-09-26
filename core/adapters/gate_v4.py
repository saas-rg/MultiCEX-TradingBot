# core/adapters/gate_v4.py
from __future__ import annotations
from decimal import Decimal
from typing import Tuple, List, Dict, Any, Optional

from core.exchange_base import ExchangeAdapter

# Делегируем в существующую реализацию
from exchanges import gate as gate


class GateV4Adapter(ExchangeAdapter):
    def __init__(self, config: Dict[str, Any] | None = None):
        # Если exchanges/gate.py требует явной инициализации — можно сделать здесь.
        # Сейчас оставляем поведение 1:1.
        self._config = config or {}

    def exchange_name(self) -> str:
        return "gate"

    # ===== meta =====
    def get_server_time_epoch(self) -> int:
        return int(gate.get_server_time_epoch())

    # ===== rules =====
    def get_pair_rules(self, pair: str) -> Tuple[int, int, Decimal, Decimal]:
        return gate.get_pair_rules(pair)

    # ===== market data =====
    def get_last_price(self, pair: str) -> Decimal:
        return gate.get_last_price(pair)

    def get_prev_minute_close(self, pair: str) -> Decimal:
        return gate.get_prev_minute_close(pair)

    # ===== trading / orders =====
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

    # ===== account =====
    def get_available(self, asset: str) -> Decimal:
        return gate.get_available(asset)

    # ===== reporting (NEW in v0.7.3 plumbing) =====
    def fetch_trades(
        self,
        *,
        pair: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int | None = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Унифицированная история трейдов для отчётности.
        Возвращает список словарей вида:
        {
            "ts": int,                # unix секунды
            "price": str,
            "amount": str,
            "side": "buy" | "sell",
            "fee": str,
            "fee_currency": str,
            "trade_id": str,
        }
        Стабильно отсортировано по (ts, trade_id).
        """
        # Базовый путь — делегируем в exchanges.gate.fetch_trades (см. ниже)
        if hasattr(gate, "fetch_trades"):
            return gate.fetch_trades(pair=pair, start_ts=start_ts, end_ts=end_ts, limit=limit, **kwargs)

        # Fallback (если ещё не внесли функцию в exchanges.gate):
        # попробуем собрать на основе list_my_trades
        trades = gate.list_my_trades(pair=pair, limit=limit or 200, since_ts=start_ts or 0)

        def _to_row(t: Dict[str, Any]) -> Dict[str, Any]:
            ts = int(t.get("create_time", 0))
            if end_ts and ts > end_ts:
                # отфильтруем выше по времени уже после маппинга
                pass
            return {
                "ts": ts,
                "price": str(t.get("price", "0")),
                "amount": str(t.get("amount", "0")),
                "side": str(t.get("side", "")).lower(),
                "fee": str(t.get("fee", "0")),
                "fee_currency": str(t.get("fee_currency", "USDT")),
                "trade_id": str(t.get("id", "")),
            }

        rows = [_to_row(t) for t in trades if (start_ts or 0) <= int(t.get("create_time", 0)) <= (end_ts or 9_999_999_999)]
        # Стабильная сортировка: сперва по ts, затем по trade_id
        rows.sort(key=lambda r: (int(r.get("ts", 0)), str(r.get("trade_id", ""))))
        if limit is not None and limit > 0:
            rows = rows[:limit]
        return rows
