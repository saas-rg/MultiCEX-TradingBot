# core/exchange_proxy.py
from __future__ import annotations

from decimal import Decimal
from typing import Tuple, List, Dict, Any, Optional, Callable

from core.exchange_base import ExchangeAdapter
from core.adapters.gate_v4 import GateV4Adapter  # существующий адаптер Gate (v0.7.x)

# === Мульти-CEX: реестр + ленивые фабрики ===

DEFAULT_EXCHANGE = "gate"

_registry: Dict[str, Callable[[Any], ExchangeAdapter]] = {}
_instances: Dict[str, ExchangeAdapter] = {}
_config_ctx: Any | None = None
_defaults_registered: bool = False


class ExchangeNotRegistered(RuntimeError):
    pass


def register_adapter(code: str, factory: Callable[[Any], ExchangeAdapter]) -> None:
    """
    Регистрирует фабрику адаптера. Фабрика должна принимать единый config-контекст.
    Повторная регистрация перезапишет фабрику.
    """
    _registry[code.strip().lower()] = factory


def _register_defaults_once() -> None:
    global _defaults_registered
    if _defaults_registered:
        return

    # Gate уже есть в проекте
    register_adapter("gate", lambda cfg: GateV4Adapter(cfg))

    # HTX ожидается как core.adapters.htx.HTXAdapter (добавим, когда файл появится).
    # Реестр готов к подключению — отсутствие модуля не мешает работе Gate.
    def _htx_factory(cfg: Any) -> ExchangeAdapter:
        from core.adapters.htx import HTXAdapter  # type: ignore
        return HTXAdapter(cfg)

    register_adapter("htx", _htx_factory)

    _defaults_registered = True


def init_registry(config: Any) -> None:
    """
    Инициализирует мультибиржевой контекст (сохраняем config для фабрик).
    Не создаёт инстансы немедленно — ленивое создание при первом get_adapter(...).
    """
    global _config_ctx
    _config_ctx = config
    _register_defaults_once()


# ---- Обратная совместимость: init_adapter(config) остаётся инициализатором Gate ----

_adapter: Optional[ExchangeAdapter] = None  # старый синглтон (gate)

def init_adapter(config: Any) -> None:
    """
    Инициализация "старого" адаптера (Gate по умолчанию).
    Внутри также настраивает мультибиржевой реестр.
    """
    global _adapter
    init_registry(config)
    # Сохраняем старое поведение: один активный адаптер Gate
    _adapter = GateV4Adapter(config)
    # Параллельно готовим мультибиржевый путь: инстанс gate будет такой же
    _instances[DEFAULT_EXCHANGE] = _adapter


def get_adapter(exchange: Optional[str] = None) -> ExchangeAdapter:
    """
    Возвращает (и кэширует) инстанс адаптера по коду биржи.
    Если exchange не задан — используется DEFAULT_EXCHANGE.
    """
    _register_defaults_once()
    code = (exchange or DEFAULT_EXCHANGE).strip().lower()

    if code in _instances:
        return _instances[code]

    if _config_ctx is None:
        # Совместимость со старым кодом: если init_registry не вызывали,
        # но уже вызвали init_adapter(config) — используем _adapter для gate.
        if code == DEFAULT_EXCHANGE and _adapter is not None:
            _instances[code] = _adapter
            return _adapter
        raise RuntimeError("Exchange registry is not initialized. Call init_adapter(config) or init_registry(config) first.")

    factory = _registry.get(code)
    if not factory:
        raise ExchangeNotRegistered(f"Exchange adapter is not registered: '{code}'")

    instance = factory(_config_ctx)
    _instances[code] = instance
    return instance


# Внутренняя проверка наличия «активного» адаптера для старого API (gate-only)
def _require() -> ExchangeAdapter:
    if _adapter is None:
        raise RuntimeError("Exchange adapter is not initialized. Call exchange_proxy.init_adapter() first.")
    return _adapter


# ====== Старые прокси-функции (полная совместимость по сигнатурам) ======

def get_server_time_epoch() -> int:
    return _require().get_server_time_epoch()

def get_pair_rules(pair: str) -> Tuple[int, int, Decimal, Decimal]:
    return _require().get_pair_rules(pair)

def get_last_price(pair: str) -> Decimal:
    return _require().get_last_price(pair)

def get_prev_minute_close(pair: str) -> Decimal:
    return _require().get_prev_minute_close(pair)

def place_limit_buy(pair: str, price: str, amount: str, account: str | None = None) -> str:
    return _require().place_limit_buy(pair, price, amount, account)

def market_sell(pair: str, amount_base: str, account: str | None = None) -> str:
    return _require().market_sell(pair, amount_base, account)

def cancel_order(pair: str, order_id: str) -> None:
    return _require().cancel_order(pair, order_id)

def cancel_all_open_orders(pair: str) -> None:
    return _require().cancel_all_open_orders(pair)

def list_open_orders(pair: str) -> List[Dict[str, Any]]:
    return _require().list_open_orders(pair)

def get_order_detail(pair: str, order_id: str) -> Dict[str, Any]:
    return _require().get_order_detail(pair, order_id)

def get_available(asset: str) -> Decimal:
    return _require().get_available(asset)

def fetch_trades(pair: str, *, exchange: Optional[str] = None,
                 start_ts: Optional[int] = None, end_ts: Optional[int] = None,
                 limit: Optional[int] = None, **kwargs) -> List[Dict[str, Any]]:
    """
    Унифицированный доступ к истории сделок для отчётности.
    Поддерживает мультибиржевость: если передан exchange — обращаемся к нужному адаптеру,
    иначе — к «старому» активному (gate).
    """
    if exchange:
        ad = get_adapter(exchange)
        return ad.fetch_trades(pair=pair, start_ts=start_ts, end_ts=end_ts, limit=limit, **kwargs)
    # старое поведение (gate-only)
    return _require().fetch_trades(pair=pair, start_ts=start_ts, end_ts=end_ts, limit=limit, **kwargs)


# ====== Новые утилиты для v0.8.0 (без влияния на старый код) ======

def available_exchanges() -> List[str]:
    """
    Вернёт список зарегистрированных кодов бирж (напр. ["gate", "htx"]).
    Полезно для /admin и /status.
    """
    _register_defaults_once()
    return sorted(_registry.keys())

def clear_cached_instances() -> None:
    """
    Сбросить кэш инстансов (удобно при hot-reload в дев-режиме).
    Не трогает _adapter (старую совместимость).
    """
    _instances.clear()
