# core/param_store.py
from decimal import Decimal
from threading import RLock
from typing import Dict, Any

from config import (
    PAIR, DEVIATION_PCT, QUOTE_USDT, LOT_SIZE_BASE,
    GAP_MODE, GAP_SWITCH_PCT, ACCOUNT_TYPE
)

_lock = RLock()
_state: Dict[str, Any] = {
    # стартовые значения — из config.py (как раньше)
    "PAIR": PAIR,
    "DEVIATION_PCT": Decimal(DEVIATION_PCT),
    "QUOTE": Decimal(QUOTE_USDT),
    "LOT_SIZE_BASE": Decimal(LOT_SIZE_BASE),
    "GAP_MODE": GAP_MODE,
    "GAP_SWITCH_PCT": Decimal(GAP_SWITCH_PCT),
    "ACCOUNT": ACCOUNT_TYPE or "spot",
    # при необходимости сюда же можно добавить другие параметры
}

def get_params() -> Dict[str, Any]:
    with _lock:
        return dict(_state)

def update_params(patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Принимает словарь с частичными обновлениями.
    Безопасно приводит к типам, игнорирует неизвестные ключи.
    Возвращает актуальное состояние после обновления.
    """
    with _lock:
        for k, v in (patch or {}).items():
            if k not in _state:
                continue
            if k in ("DEVIATION_PCT", "QUOTE", "LOT_SIZE_BASE", "GAP_SWITCH_PCT"):
                try:
                    _state[k] = Decimal(str(v))
                except Exception:
                    continue
            elif k in ("PAIR", "GAP_MODE", "ACCOUNT"):
                _state[k] = str(v)
            else:
                _state[k] = v
        return dict(_state)
