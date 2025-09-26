# core/strategy.py
from decimal import Decimal, ROUND_DOWN
from typing import Tuple
import threading
import concurrent.futures
import time
import traceback
import logging

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
from core.telemetry import send_event

# ⬇️ MultiCEX: берём адаптер по бирже
from core.exchange_proxy import get_adapter

_pair_rules_lock = threading.Lock()
_pair_rules: dict[tuple[str, str], tuple[int, int, Decimal, Decimal]] = {}  # (exchange, pair) -> rules
FEE_BUFFER = Decimal("0.9985")

log = logging.getLogger(__name__)

# --- антиспам автоуведомлений автоснижения BUY ---
AUTO_RESIZE_COOLDOWN_SEC = 5 * 60  # 5 минут
_auto_resize_last_ts: dict[str, float] = {}  # pair -> last send ts (epoch seconds)
_auto_resize_lock = threading.Lock()         # потокобезопасность


def _compute_base_and_target(ad, pair: str, gap_mode: str, gap_switch_pct: Decimal, deviation_pct: Decimal) -> Tuple[Decimal, Decimal, Decimal, str]:
    """Источники цены берём из адаптера конкретной биржи."""
    prev_close = ad.get_prev_minute_close(pair)
    last = ad.get_last_price(pair)
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


def _ensure_pair_rules(ad, exchange: str, pair: str):
    key = (exchange, pair)
    with _pair_rules_lock:
        if key in _pair_rules:
            return _pair_rules[key]
    rules = ad.get_pair_rules(pair)
    with _pair_rules_lock:
        _pair_rules[key] = rules
    return rules


def _drain(pair: str, base_sym: str, aprec: int, min_base: Decimal, ad) -> None:
    """Совместимость: стараемся передать adapter, если реализация не принимает — вызываем как раньше."""
    try:
        # новый путь (желательно, чтобы core.drain поддерживал adapter=...)
        drain_base_position(pair, base_sym, aprec, min_base, adapter=ad)  # type: ignore
    except TypeError:
        # старая реализация без adapter
        drain_base_position(pair, base_sym, aprec, min_base)


def _prepare_and_place(cfg: dict):
    pair = cfg["pair"]
    exchange = (cfg.get("exchange") or "gate").strip().lower()
    ad = get_adapter(exchange)

    try:
        base_sym, quote_sym = pair.split("_", 1)
    except Exception:
        return {"pair": pair, "ok": False, "error": "invalid pair format"}

    # cancel_all_open_orders перед новой покупкой
    try:
        ad.cancel_all_open_orders(pair)
    except Exception as e:
        print(f"[{exchange}:{pair}] cancel_all_open_orders перед покупкой: {e}")

    # правила символа
    try:
        pprec, aprec, min_base, min_quote = _ensure_pair_rules(ad, exchange, pair)
    except Exception as e:
        return {"pair": pair, "ok": False, "error": f"get_pair_rules error: {e}"}

    # слив до пыли перед новой покупкой
    try:
        _drain(pair, base_sym, aprec, min_base, ad)
    except Exception as e:
        print(f"[{exchange}:{pair}] drain before buy error: {e}")

    # расчёт целевой цены
    try:
        base_price, target_price, gap_pct, src = _compute_base_and_target(
            ad, pair, cfg["gap_mode"], cfg["gap_switch_pct"], cfg["deviation_pct"]
        )
    except Exception as e:
        return {"pair": pair, "ok": False, "error": f"compute base/target error: {e}"}

    if target_price <= 0:
        return {"pair": pair, "ok": False, "error": "target_price <= 0"}

    # доступный quote
    try:
        avail_quote = ad.get_available(quote_sym)
    except Exception as e:
        return {"pair": pair, "ok": False, "error": f"get_available({quote_sym}) error: {e}"}

    try:
        # --- расчёт исходного объёма ---
        if cfg["lot_size_base"] > 0:
            amount_base = dquant(cfg["lot_size_base"], aprec)
            order_quote_value = (amount_base * target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)
        else:
            plan_quote = cfg["quote"] if cfg["quote"] > 0 else (avail_quote * FEE_BUFFER).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)
            raw_amount = (plan_quote / target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)
            amount_base = dquant(raw_amount, aprec)
            order_quote_value = (amount_base * target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)

        # запомним исходный запрос до автокоррекции (для телеметрии)
        requested_amount_base = amount_base

        # --- автокоррекция по доступному балансу ---
        max_affordable_quote = (avail_quote * FEE_BUFFER).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)
        if order_quote_value > max_affordable_quote and target_price > 0:
            amount_base = dquant((max_affordable_quote / target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN), aprec)
            order_quote_value = (amount_base * target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)

        # --- проверка минимумов биржи (quote/base) ---
        if min_quote and order_quote_value < min_quote and target_price > 0:
            need_amount = (min_quote / target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)
            adj_amount = dquant(need_amount, aprec)
            max_amount = dquant((max_affordable_quote / target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN), aprec)
            amount_base = max(min(adj_amount, max_amount), amount_base)

        if min_base and amount_base < min_base:
            amount_base = min_base
            order_quote_value = (amount_base * target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)

        # повторный «стопор» по балансу на случай подъёма из-за минимумов
        if order_quote_value > max_affordable_quote and target_price > 0:
            amount_base = dquant((max_affordable_quote / target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN), aprec)
            order_quote_value = (amount_base * target_price).quantize(Decimal("1e-18"), rounding=ROUND_DOWN)

        # --- уведомление об автоснижении объёма (антиспам) ---
        if amount_base < requested_amount_base:
            try:
                now = time.time()
                with _auto_resize_lock:
                    last_ts = _auto_resize_last_ts.get(pair, 0.0)
                    ok_to_send = (now - last_ts) >= AUTO_RESIZE_COOLDOWN_SEC

                if ok_to_send:
                    delta_pct = (requested_amount_base - amount_base) / requested_amount_base * Decimal(100) if requested_amount_base > 0 else Decimal(0)
                    final_quote = order_quote_value  # уже посчитан выше
                    msg = (
                        f"[{exchange}:{pair}] Автокоррекция BUY из-за нехватки средств: "
                        f"{fmt(requested_amount_base, aprec)} → {fmt(amount_base, aprec)} "
                        f"(-{delta_pct.quantize(Decimal('1.00'))}%). "
                        f"Цена={fmt(target_price, pprec)}, notional≈{final_quote} {quote_sym}. "
                        f"Доступно={avail_quote} {quote_sym}, FEE_BUFFER={FEE_BUFFER}."
                    )
                    log.warning(msg)
                    send_event("auto_resize_buy", msg)
                    with _auto_resize_lock:
                        _auto_resize_last_ts[pair] = now
                else:
                    remaining = 0
                    with _auto_resize_lock:
                        remaining = int(max(0, AUTO_RESIZE_COOLDOWN_SEC - (now - _auto_resize_last_ts.get(pair, 0.0))))
                    log.debug(f"[{exchange}:{pair}] auto_resize_buy suppressed by cooldown ({remaining}s left)")
            except Exception as _e:
                log.debug("auto_resize_buy notify skipped: %r", _e)

    except Exception as e:
        return {"pair": pair, "ok": False, "error": f"amount calc error: {e}"}

    if amount_base <= Decimal(0):
        set_last_order_id(pair, None)
        return {"pair": pair, "ok": False, "error": "amount <= 0 (not enough quote balance)"}

    try:
        oid = ad.place_limit_buy(
            pair,
            price=fmt(target_price, pprec),
            amount=fmt(amount_base, aprec),
            account=ACCOUNT_TYPE
        )
        set_last_order_id(pair, oid)
        print(f"[{exchange}:{pair}] BUY(limit) placed: id={oid}, amount={fmt(amount_base, aprec)}, quote≈{order_quote_value}, target={fmt(target_price, pprec)}")
        return {"pair": pair, "ok": True, "order_id": oid, "amount": amount_base, "price": target_price}
    except Exception as e:
        traceback.print_exc()
        set_last_order_id(pair, None)
        return {"pair": pair, "ok": False, "error": f"place_limit_buy error: {e}"}


def _cleanup_pair(cfg: dict):
    pair = cfg["pair"]
    exchange = (cfg.get("exchange") or "gate").strip().lower()
    ad = get_adapter(exchange)

    try:
        base_sym, _ = pair.split("_", 1)
    except Exception:
        print(f"[{exchange}:{pair}] invalid pair format in cleanup")
        return {"pair": pair, "ok": False, "error": "invalid pair format"}

    try:
        pprec, aprec, min_base, _ = _ensure_pair_rules(ad, exchange, pair)
    except Exception:
        aprec = 8
        min_base = Decimal("0")

    try:
        _drain(pair, base_sym, aprec, min_base, ad)
    except Exception as e:
        print(f"[{exchange}:{pair}] pre-cancel drain error: {e}")

    oid = get_last_order_id(pair)
    if oid:
        try:
            ad.cancel_order(pair, oid)
            print(f"[{exchange}:{pair}] cancel order requested: {oid}")
        except Exception as e:
            print(f"[{exchange}:{pair}] cancel_order error: {e}")
        finally:
            set_last_order_id(pair, None)

    try:
        ad.cancel_all_open_orders(pair)
    except Exception as e:
        print(f"[{exchange}:{pair}] cancel_all_open_orders в cleanup: {e}")

    try:
        _drain(pair, base_sym, aprec, min_base, ad)
    except Exception as e:
        print(f"[{exchange}:{pair}] final drain error: {e}")

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
