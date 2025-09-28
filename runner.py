# runner.py
import signal
import sys
import time

from core.params import list_pairs, ensure_schema
from core.strategy import trading_cycle
from core import exchange_proxy
from core.telemetry import send_event
from core.db_migrate import run_all as run_db_migrations
from core.exchange_proxy import get_adapter
from core.params import get_shutdown

def _cancel_all_pairs_orders():
    try:
        pairs = list_pairs(include_disabled=False)
    except Exception as e:
        print(f"[SHUTDOWN] Не удалось получить пары: {e}")
        return
    for cfg in pairs:
        ex = (cfg.get("exchange") or "gate").strip().lower()
        p = cfg["pair"]
        try:
            ad = get_adapter(ex)
            ad.cancel_all_open_orders(p)
            print(f"[CLEANUP] Отменены все открытые ордера по {ex}:{p}")
        except Exception as e:
            print(f"[CLEANUP] Ошибка отмены по {ex}:{p}: {e}")

def _handle_signal(signum, frame):
    try:
        send_event("worker_stop", f"Процесс получает сигнал <code>{signum}</code>, выполняю очистку…")
    except Exception:
        pass
    try:
        _cancel_all_pairs_orders()
    finally:
        sys.exit(0)

def main():
    ensure_schema()
    try:
        run_db_migrations()
    except Exception as e:
        print(f"[MIGRATE] Ошибка автомиграции: {e}")

    # НЕ сбрасываем shutdown здесь — это делает кнопка «Запустить» в админке
    import config
    exchange_proxy.init_adapter(config)

    # Надзорный (supervisor) цикл: процесс живёт всегда,
    # при shutdown=true — ждёт команды запуска, при false — крутит торговый цикл.
    while True:
        try:
            if get_shutdown():
                print("[STANDBY] shutdown=true — ждём команду «Запустить» из админки…")
                time.sleep(2)
                continue

            # Реконсиляция перед стартом + телеметрия
            try:
                _cancel_all_pairs_orders()
            except Exception:
                pass
            send_event("worker_start", "Воркер запущен и готов к торговому циклу.")

            # Вернёмся сюда, когда trading_cycle() завершится по стопу
            trading_cycle()
            # После return из trading_cycle() цикл while продолжится:
            # если в БД стоит shutdown=true — войдём в standby, если false — перезапустим торговлю.

        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[SUPERVISOR] trading_cycle crashed: {e}")
            time.sleep(2)
            continue

if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        main()
    except KeyboardInterrupt:
        _handle_signal(signal.SIGINT, None)
