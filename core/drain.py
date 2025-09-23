import time
from decimal import Decimal
from core.quant import dquant, fmt
from exchanges.gate import get_available, market_sell
from config import SELL_DRAIN_SLEEP, DRAIN_MAX_SECONDS, DRAIN_SLEEP_MAX, ACCOUNT_TYPE

def drain_base_position(pair: str, base: str, amount_prec: int, min_base: Decimal) -> Decimal:
    start = time.time()
    min_step = Decimal(1).scaleb(-amount_prec)
    min_sellable = max(min_base or Decimal(0), min_step)

    attempt = 0
    while True:
        if time.time() - start > DRAIN_MAX_SECONDS:
            left = get_available(base)
            if left > 0:
                print(f"[DRAIN] Время истекло, остаток {left} {base}.")
            return left

        avail = get_available(base)
        sellable = dquant(avail, amount_prec)

        if sellable < min_sellable:
            if avail > 0:
                print(f"[DRAIN] Остаток пыль: {avail} {base} (< {min_sellable})")
            return avail

        sid = market_sell(pair, fmt(sellable, amount_prec), account=ACCOUNT_TYPE)
        print(f"[DRAIN] Market SELL: id={sid}, amount={fmt(sellable, amount_prec)}; проверяю остаток...")

        attempt += 1
        sleep_s = min(SELL_DRAIN_SLEEP * (1 + 0.5 * (attempt - 1)), DRAIN_SLEEP_MAX)
        time.sleep(sleep_s)
