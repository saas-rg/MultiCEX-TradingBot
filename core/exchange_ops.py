# core/exchange_ops.py
from __future__ import annotations
from decimal import Decimal
from typing import Optional

from core.exchange_proxy import get_adapter
from core.quant import dquant
from core.drain import drain_base_position

def cancel_and_drain(exchange: str, pair: str) -> None:
    """
    Отменяет все открытые ордера по паре и сливает базовый остаток до «пыли».
    Безопасно вызывать многократно.
    """
    ex = (exchange or "gate").strip().lower()
    ad = get_adapter(ex)

    try:
        base_sym, _ = pair.split("_", 1)
    except Exception:
        # Нечего чистить, если формат неверный
        return

    # Попытаемся получить правила для корректного слива
    aprec: int = 8
    min_base: Decimal = Decimal("0")
    try:
        _, aprec, min_base, _ = ad.get_pair_rules(pair)
    except Exception:
        pass

    # 1) Отменить все открытые ордера
    try:
        ad.cancel_all_open_orders(pair)
        print(f"[{ex}:{pair}] delete → cancel_all_open_orders done")
    except Exception as e:
        print(f"[{ex}:{pair}] delete → cancel_all_open_orders error: {e}")

    # 2) Финальный дренаж
    try:
        drain_base_position(pair, base_sym, aprec, min_base, adapter=ad)
        print(f"[{ex}:{pair}] delete → final drain done")
    except Exception as e:
        print(f"[{ex}:{pair}] delete → final drain error: {e}")
