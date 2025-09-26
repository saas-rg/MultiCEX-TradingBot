# runner.py
import signal
import sys
from core.params import list_pairs, ensure_schema
from core.strategy import trading_cycle
from core import exchange_proxy
from core.exchange_proxy import cancel_all_open_orders
from core.telemetry import send_event
from core.db_migrate import run_all as run_db_migrations

def _cancel_all_pairs_orders():
    try:
        pairs = list_pairs(include_disabled=False)
    except Exception as e:
        print(f"[SHUTDOWN] Не удалось получить пары: {e}")
        return
    for cfg in pairs:
        p = cfg["pair"]
        try:
            cancel_all_open_orders(p)
            print(f"[CLEANUP] Отменены все открытые ордера по {p}")
        except Exception as e:
            print(f"[CLEANUP] Ошибка отмены по {p}: {e}")

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
    # v0.7.3: идемпотентные миграции (bot_pairs.exchange)
    try:
        run_db_migrations()
    except Exception as e:
        print(f"[MIGRATE] Ошибка автомиграции: {e}")

    # Инициализация адаптера биржи (Gate в v0.7.1) — используем корневой config.py
    import config                      # <-- ВАЖНО: из корня проекта
    exchange_proxy.init_adapter(config)

    # Реконсиляция перед стартом + телеметрия
    try:
        _cancel_all_pairs_orders()
    except Exception:
        pass
    send_event("worker_start", "Воркер запущен и готов к торговому циклу.")

    trading_cycle()

if __name__ == "__main__":
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        main()
    except KeyboardInterrupt:
        _handle_signal(signal.SIGINT, None)
