# core/strategy.py
from decimal import Decimal, ROUND_DOWN
from typing import Tuple
import threading
import concurrent.futures
import time
import traceback

from config import (
    API_KEY, API_SECRET, TESTNET, HOST, ACCOUNT_TYPE,
)
from core.quant import dquant, fmt
from core.drain import drain_base_position
from core.sync import sleep_until_next_minute
from core.state import set_last_order_id, get_last_order_id
from core.params import list_pairs, get_paused
from core.reporting import tick as reporting_tick
from core.heartbeat import tick as heartbeat_tick, init as heartbeat_init
from exchanges.gate import (
    get_pair_rules, get_last_price, get_prev_minute_close,
    place_limit_buy, cancel_order, get_available, cancel_all_open_orders
)

_pair_rules_lock = threading.Lock()
_pair_rules: dict[str, tuple[int, int, Decimal, Decimal]] = {}
FEE_BUFFER = Decimal("0.9985")

def _compute_base_and_target(pair: str, gap_mode: str, gap_switch_pct: Decimal, deviation_pct: Decimal) -> Tuple[Decimal, Decimal, Decimal, str]:
    prev_close = get_prev_minute_close(pair)
    last = get_last_price(pair)
    base_price = prev_close
    gap_pct = (prev_close - last) / prev_close * Decimal(100) if prev_close > 0 else Decimal(0)

    def _switch_downonly(gap: Decimal) -> bool:
        return gap > gap_switch_pct
    def _switch_symmetric(gap: Decimal) -> bool:
        return abs(gap) > gap_switch_pct

    src = "close_1m"
    if gap_mode == "down_only":
        if _switch_downonly(gap_pct):
            base_price = last
            src = "last"
    elif gap_mode == "symmetric":
        if _switch_symmetric(gap_pct):
            base_price = last
            src = "last"

    target_price = base_price * (Decimal(1) - deviation_pct / Decimal(100))
    return base_price, target_price, gap_pct, src

def _ensure_pair_rules(pair: str):
    with _pair_rules_lock:
        if pair in _pair_rules:
            return _pair_rules[pair]
    rules = get_pair_rules(pair)
    with _pair_rules_lock:
        _pair_rules[pair] = rules
    return rules

def _prepare_and_place(cfg: dict):
    pair = cfg["pair"]
    try:
        base_sym, quote_sym = pair.split("_", 1)
    except Exception:
        return {"pair": pair, "ok": False, "error": "invalid pair format"}

    try:
        cancel_all_open_orders(pair)
    except Exception as e:
        print(f"[{pair}] cancel_all_open_orders перед покупкой: {e}")

    try:
        pprec, aprec, min_base, min_quote = _ensure_pair_rules(pair)
    except Exception as e:
        return {"pair": pair, "ok": False, "error": f"get_pair_rules error: {e}"}

    try:
        drain_base_position(pair, base_sym, aprec, min_base)
    except Exception as e:
        print(f"[{pair}] drain before buy error: {e}")

    try:
        base_price, target_price, gap_pct, src = _compute_base_and_target(
            pair, cfg["gap_mode"], cfg["gap_switch_pct"], cfg["deviation_pct"]
        )
    except Exception as e:
        return {"pair": pair, "ok": False, "error": f"compute base/target error: {e}"}

    if target_price <= 0:
        return {"pair": pair, "ok": False, "error": "target_price <= 0"}

    try:
        avail_quote = get_available(quote_sym)
    except Exception as e:
        return {"pair": pair, "ok": False, "error": f"get_available({quote_sym}) error: {e}"}

    try:
        if cfg["lot_size_base"] > 0:
            amount_base = dquant(cfg["lot_size_base"], aprec)
            order_quote_value = (amount_base * target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)
        else:
            plan_quote = cfg["quote"] if cfg["quote"] > 0 else (avail_quote * FEE_BUFFER).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)
            raw_amount = (plan_quote / target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)
            amount_base = dquant(raw_amount, aprec)
            order_quote_value = (amount_base * target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)

        max_affordable_quote = (avail_quote * FEE_BUFFER).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)
        if order_quote_value > max_affordable_quote and target_price > 0:
            amount_base = dquant((max_affordable_quote / target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN), aprec)
            order_quote_value = (amount_base * target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)

        if min_quote and order_quote_value < min_quote and target_price > 0:
            need_amount = (min_quote / target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)
            adj_amount = dquant(need_amount, aprec)
            max_amount = dquant((max_affordable_quote / target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN), aprec)
            amount_base = max(min(adj_amount, max_amount), amount_base)

        if min_base and amount_base < min_base:
            amount_base = min_base
            order_quote_value = (amount_base * target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)

        if order_quote_value > max_affordable_quote and target_price > 0:
            amount_base = dquant((max_affordable_quote / target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN), aprec)
            order_quote_value = (amount_base * target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)

    except Exception as e:
        return {"pair": pair, "ok": False, "error": f"amount calc error: {e}"}

    if amount_base <= Decimal(0):
        set_last_order_id(pair, None)
        return {"pair": pair, "ok": False, "error": "amount <= 0 (not enough quote balance)"}

    try:
        oid = place_limit_buy(
            pair,
            price=fmt(target_price, pprec),
            amount=fmt(amount_base, aprec),
            account=ACCOUNT_TYPE
        )
        set_last_order_id(pair, oid)
        print(f"[{pair}] BUY(limit) placed: id={oid}, amount={fmt(amount_base, aprec)}, quote≈{order_quote_value}, target={fmt(target_price, pprec)}")
        return {"pair": pair, "ok": True, "order_id": oid, "amount": amount_base, "price": target_price}
    except Exception as e:
        traceback.print_exc()
        set_last_order_id(pair, None)
        return {"pair": pair, "ok": False, "error": f"place_limit_buy error: {e}"}

def _cleanup_pair(cfg: dict):
    pair = cfg["pair"]
    try:
        base_sym, _ = pair.split("_", 1)
    except Exception:
        print(f"[{pair}] invalid pair format in cleanup")
        return {"pair": pair, "ok": False, "error": "invalid pair format"}

    try:
        pprec, aprec, min_base, _ = _ensure_pair_rules(pair)
    except Exception:
        aprec = 8
        min_base = Decimal("0")

    try:
        drain_base_position(pair, base_sym, aprec, min_base)
    except Exception as e:
        print(f"[{pair}] pre-cancel drain error: {e}")

    oid = get_last_order_id(pair)
    if oid:
        try:
            cancel_order(pair, oid)
            print(f"[{pair}] cancel order requested: {oid}")
        except Exception as e:
            print(f"[{pair}] cancel_order error: {e}")
        finally:
            set_last_order_id(pair, None)

    try:
        cancel_all_open_orders(pair)
    except Exception as e:
        print(f"[{pair}] cancel_all_open_orders в cleanup: {e}")

    try:
        drain_base_position(pair, base_sym, aprec, min_base)
    except Exception as e:
        print(f"[{pair}] final drain error: {e}")

    return {"pair": pair, "ok": True}

def trading_cycle():
    if not API_KEY or not API_SECRET:
        print("❗ Не заданы ключи API. Заполните GATE_API_KEY и GATE_API_SECRET в .env/Config Vars")

    print(f"TESTNET={TESTNET} HOST={HOST} ACCOUNT={ACCOUNT_TYPE or 'default'}")
    print("Старт мультипарного параллельного цикла (с отчётами).")
    # Инициализация heartbeat (проверка тишины на старте)
    heartbeat_init()

    while True:
        try:
            if get_paused():
                print("[PAUSE] Paused by control flag. Sleeping until next minute...")
                sleep_until_next_minute()
                reporting_tick()
                heartbeat_tick()
                continue

            pairs = list_pairs(include_disabled=False)
            if not pairs:
                print("[CONFIG] Не задано ни одной активной пары. Сплю до следующей минуты.")
                sleep_until_next_minute()
                reporting_tick()
                heartbeat_tick()
                continue

            max_workers = min(16, max(1, len(pairs) * 2))

            # --- BUY лимитники ---
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = { ex.submit(_prepare_and_place, cfg): cfg["pair"] for cfg in pairs }
                for fut in concurrent.futures.as_completed(futs):
                    pair = futs[fut]
                    try:
                        res = fut.result()
                        if not res.get("ok"):
                            print(f"[{pair}] place error: {res.get('error')}")
                    except Exception as e:
                        print(f"[{pair}] place fatal: {e}")

            sleep_until_next_minute()

            # --- cleanup/market-sell ---
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = { ex.submit(_cleanup_pair, cfg): cfg["pair"] for cfg in pairs }
                for fut in concurrent.futures.as_completed(futs):
                    pair = futs[fut]
                    try:
                        _ = fut.result()
                    except Exception as e:
                        print(f"[{pair}] cleanup fatal: {e}")

            # периодические сервисные тики
            reporting_tick()
            heartbeat_tick()

        except Exception as e:
            print(f"Ошибка цикла: {e}")
            traceback.print_exc()
            time.sleep(2)
            try:
                reporting_tick()
                heartbeat_tick()
            except Exception:
                pass
            continue
