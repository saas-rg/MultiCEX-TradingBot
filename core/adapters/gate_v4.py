# core/adapters/gate_v4.py
from __future__ import annotations

import time
import random
import functools
from decimal import Decimal
from typing import Tuple, List, Dict, Any, Optional

from core.exchange_base import ExchangeAdapter
from config import RETRIES

# Делегируем в существующую реализацию
from exchanges import gate as gate


def _is_transient(err: Exception) -> bool:
    s = str(err).lower()
    # простая эвристика по временным ошибкам сети/HTTP
    return any(k in s for k in [
        "timeout", "timed out", "connection", "reset", "econn", "read timed",
        "429", " 5", "server error", "temporarily", "gateway", "unavailable", "rate"
    ])


def _retryable(fn):
    """Экспоненциальные ретраи с лёгким джиттером, управляются config.RETRIES."""
    @functools.wraps(fn)
    def wrap(*args, **kwargs):
        attempts = max(1, int(RETRIES))
        last = None
        for i in range(attempts):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                # если не похоже на временную ошибку — не ретраим
                if not _is_transient(e) or i == attempts - 1:
                    raise
                last = e
                # 0.25 * 2**i + jitter(0..0.20)
                delay = 0.25 * (2 ** i) + random.uniform(0.0, 0.20)
                time.sleep(delay)
        # на всякий случай
        raise last
    return wrap


class GateV4Adapter(ExchangeAdapter):
    def __init__(self, config: Dict[str, Any] | None = None):
        # Если exchanges/gate.py требует явной инициализации — можно сделать здесь.
        # Сейчас оставляем поведение 1:1.
        self._config = config or {}
        # Кеш правил символа: pair -> (price_precision, amount_precision, min_base, min_quote)
        self._rules_cache: dict[str, Tuple[int, int, Decimal, Decimal]] = {}

    def exchange_name(self) -> str:
        return "gate"

    # ===== meta =====
    @_retryable
    def get_server_time_epoch(self) -> int:
        return int(gate.get_server_time_epoch())

    # ===== rules =====
    @_retryable
    def _get_pair_rules_uncached(self, pair: str) -> Tuple[int, int, Decimal, Decimal]:
        return gate.get_pair_rules(pair)

    def get_pair_rules(self, pair: str) -> Tuple[int, int, Decimal, Decimal]:
        cached = self._rules_cache.get(pair)
        if cached:
            return cached
        rules = self._get_pair_rules_uncached(pair)
        self._rules_cache[pair] = rules
        return rules

    # ===== market data =====
    @_retryable
    def get_last_price(self, pair: str) -> Decimal:
        return gate.get_last_price(pair)

    @_retryable
    def get_prev_minute_close(self, pair: str) -> Decimal:
        return gate.get_prev_minute_close(pair)

    # ===== trading / orders =====
    @_retryable
    def place_limit_buy(self, pair: str, price: str, amount: str, account: Optional[str] = None) -> str:
        return gate.place_limit_buy(pair=pair, price=price, amount=amount, account=account)

    @_retryable
    def market_sell(self, pair: str, amount_base: str, account: Optional[str] = None) -> str:
        return gate.market_sell(pair=pair, amount_base=amount_base, account=account)

    @_retryable
    def cancel_order(self, pair: str, order_id: str) -> None:
        gate.cancel_order(pair=pair, order_id=order_id)

    @_retryable
    def cancel_all_open_orders(self, pair: str) -> None:
        gate.cancel_all_open_orders(pair)

    @_retryable
    def list_open_orders(self, pair: str) -> List[Dict[str, Any]]:
        return gate.list_open_orders(pair)

    @_retryable
    def get_order_detail(self, pair: str, order_id: str) -> Dict[str, Any]:
        return gate.get_order_detail(pair=pair, order_id=order_id)

    # ===== account =====
    @_retryable
    def get_available(self, asset: str) -> Decimal:
        return gate.get_available(asset)

    # ===== reporting (NEW in v0.7.3 plumbing) =====
    @_retryable
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
        # Базовый путь — делегируем в exchanges.gate.fetch_trades (если доступен)
        if hasattr(gate, "fetch_trades"):
            rows = gate.fetch_trades(pair=pair, start_ts=start_ts, end_ts=end_ts, limit=limit, **kwargs)
            # предполагаем, что exchanges.gate уже сортирует и фильтрует — просто возвращаем
            return rows

        # Fallback (если ещё нет функции в exchanges.gate):
        trades = gate.list_my_trades(pair=pair, limit=limit or 200, since_ts=start_ts or 0)

        def _to_row(t: Dict[str, Any]) -> Dict[str, Any]:
            ts = int(t.get("create_time", 0))
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
