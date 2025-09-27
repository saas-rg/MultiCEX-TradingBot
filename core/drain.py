# core/drain.py
import time
from decimal import Decimal
from typing import Optional

from core.quant import dquant, fmt
# Fallback-прокси для обратной совместимости (gate-only путь):
from core.exchange_proxy import (
    get_available as _px_get_available,
    market_sell as _px_market_sell,
    get_last_price as _px_get_last_price,
    get_pair_rules as _px_get_pair_rules,
)
from config import SELL_DRAIN_SLEEP, DRAIN_MAX_SECONDS, DRAIN_SLEEP_MAX, ACCOUNT_TYPE


def _get_avail(base: str, adapter=None) -> Decimal:
    if adapter is not None:
        return adapter.get_available(base)
    return _px_get_available(base)


def _market_sell(pair: str, amount_base_fmt: str, account: Optional[str], adapter=None) -> str:
    if adapter is not None:
        return adapter.market_sell(pair, amount_base_fmt, account=account)
    return _px_market_sell(pair, amount_base_fmt, account=account)


def _get_rules(pair: str, adapter=None):
    """
    Возвращает (price_prec, amount_prec, min_base, min_quote).
    Если adapter не передан — используем старый прокси (gate).
    """
    if adapter is not None:
        return adapter.get_pair_rules(pair)
    return _px_get_pair_rules(pair)


def _get_last(pair: str, adapter=None) -> Decimal:
    if adapter is not None:
        return adapter.get_last_price(pair)
    return _px_get_last_price(pair)


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
    :param min_base:   Минимальный размер базового актива (из правил биржи — можно передать 0, тогда возьмём из get_pair_rules)
    :param adapter:    Объект адаптера биржи (ad = exchange_proxy.get_adapter('gate'|'htx')), опционально
    :param account:    Идентификатор аккаунта/саб-аккаунта, по умолчанию берётся из config.ACCOUNT_TYPE
    :return:           Остаток base после попыток слива
    """
    start = time.time()
    account = account if account is not None else ACCOUNT_TYPE

    # Получим правила и цену (для динамического порога «пыли»)
    try:
        _pprec, _aprec_rule, min_base_rule, min_quote = _get_rules(pair, adapter=adapter)
    except Exception:
        # если не удалось — считаем «правила пустыми»
        min_base_rule = Decimal("0")
        min_quote = Decimal("0")

    # Эффективный min_base: максимум из переданного и биржевого
    eff_min_base = max(Decimal(str(min_base or 0)), Decimal(str(min_base_rule or 0)))

    # Базовый шаг округления
    min_step = Decimal(1).scaleb(-amount_prec)

    # Текущая цена (может меняться в цикле — будем обновлять)
    try:
        last_price = Decimal(str(_get_last(pair, adapter=adapter)))
    except Exception:
        last_price = Decimal("0")

    # Динамический порог «пыли» по базе:
    # - не меньше биржевого min_base
    # - не меньше min_quote / last_price (если оба заданы)
    # - не меньше минимального шага количества
    if last_price > 0 and min_quote > 0:
        by_notional = (Decimal(str(min_quote)) / last_price)
    else:
        by_notional = Decimal("0")

    dust_base_threshold = max(
        eff_min_base,
        by_notional,
        min_step
    )

    attempt = 0
    while True:
        if time.time() - start > DRAIN_MAX_SECONDS:
            left = _get_avail(base, adapter=adapter)
            if left > 0:
                print(f"[DRAIN] Время истекло, остаток {left} {base}.")
            return left

        avail = _get_avail(base, adapter=adapter)
        sellable = dquant(avail, amount_prec)

        # Обновляем цену и пересчитываем номинал
        try:
            last_price = Decimal(str(_get_last(pair, adapter=adapter)))
        except Exception:
            # если цену не получили, считаем её 0 — это заблокирует попытку рыночной продажи
            last_price = Decimal("0")

        notional = (sellable * last_price) if last_price > 0 else Decimal("0")

        # Ранний выход: «пыль» по базе или номинал ниже min_quote
        if sellable < dust_base_threshold or (min_quote and last_price > 0 and notional < min_quote):
            if avail > 0:
                # Поясним условие в логе
                if last_price > 0 and min_quote > 0 and notional < min_quote:
                    print(f"[DRAIN] Пыль по номиналу: {sellable} {base} (~{fmt(notional, 8)} quote) < min_quote {min_quote}. Пропускаю.")
                else:
                    print(f"[DRAIN] Остаток пыль: {avail} {base} (< {fmt(dust_base_threshold, amount_prec)} base). Пропускаю.")
            return avail

        # Пробуем рыночный SELL (IOC); если биржа отклонит из-за порогов — цикл повторит со сном
        sid = _market_sell(pair, fmt(sellable, amount_prec), account=account, adapter=adapter)
        print(f"[DRAIN] Market SELL: id={sid}, amount={fmt(sellable, amount_prec)}; проверяю остаток...")

        attempt += 1
        sleep_s = min(SELL_DRAIN_SLEEP * (1 + 0.5 * (attempt - 1)), DRAIN_SLEEP_MAX)
        time.sleep(sleep_s)
