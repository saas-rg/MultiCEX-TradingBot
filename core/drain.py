# core/drain.py
import time
from decimal import Decimal
from typing import Optional

from core.quant import dquant, fmt
# Fallback-прокси для обратной совместимости (gate-only путь):
from core.exchange_proxy import get_available as _px_get_available, market_sell as _px_market_sell
from config import SELL_DRAIN_SLEEP, DRAIN_MAX_SECONDS, DRAIN_SLEEP_MAX, ACCOUNT_TYPE


def _get_avail(base: str, adapter=None) -> Decimal:
    if adapter is not None:
        return adapter.get_available(base)
    return _px_get_available(base)


def _market_sell(pair: str, amount_base_fmt: str, account: Optional[str], adapter=None) -> str:
    if adapter is not None:
        return adapter.market_sell(pair, amount_base_fmt, account=account)
    return _px_market_sell(pair, amount_base_fmt, account=account)


def drain_base_position(
    pair: str,
    base: str,
    amount_prec: int,
    min_base: Decimal,
    *,
    adapter=None,
    account: Optional[str] = None,
) -> Decimal:
    """
    Сливает базовый остаток по инструменту до «пыли» серией рыночных SELL.
    MultiCEX-совместимо: можно передать adapter конкретной биржи; если не передан — используем старый прокси (gate).

    :param pair:       Например, "EDGE_USDT"
    :param base:       Базовый символ, например "EDGE"
    :param amount_prec:Точность количества (апрец)
    :param min_base:   Минимальный размер базового актива с биржи
    :param adapter:    Объект адаптера биржи (ad = exchange_proxy.get_adapter('gate'|'htx')), опционально
    :param account:    Идентификатор аккаунта/саб-аккаунта, по умолчанию берётся из config.ACCOUNT_TYPE
    :return:           Остаток base после попыток слива
    """
    start = time.time()
    min_step = Decimal(1).scaleb(-amount_prec)
    min_sellable = max(min_base or Decimal(0), min_step)
    account = account if account is not None else ACCOUNT_TYPE

    attempt = 0
    while True:
        if time.time() - start > DRAIN_MAX_SECONDS:
            left = _get_avail(base, adapter=adapter)
            if left > 0:
                print(f"[DRAIN] Время истекло, остаток {left} {base}.")
            return left

        avail = _get_avail(base, adapter=adapter)
        sellable = dquant(avail, amount_prec)

        if sellable < min_sellable:
            if avail > 0:
                print(f"[DRAIN] Остаток пыль: {avail} {base} (< {min_sellable})")
            return avail

        sid = _market_sell(pair, fmt(sellable, amount_prec), account=account, adapter=adapter)
        print(f"[DRAIN] Market SELL: id={sid}, amount={fmt(sellable, amount_prec)}; проверяю остаток...")

        attempt += 1
        sleep_s = min(SELL_DRAIN_SLEEP * (1 + 0.5 * (attempt - 1)), DRAIN_SLEEP_MAX)
        time.sleep(sleep_s)
