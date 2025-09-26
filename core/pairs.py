# core/pairs.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Tuple

from core import exchange_proxy
from config import PAIR  # в v0.7.3 только одиночная пара

# Где храним редактируемый список пар (чтобы не трогать .env)
_PAIRS_JSON_PATH = os.path.join("data", "pairs.json")


@dataclass
class PairEntry:
    exchange: str
    pair: str

    def key(self) -> Tuple[str, str]:
        return (self.exchange.strip().lower(), self.pair.strip().upper())


def _ensure_data_dir() -> None:
    d = os.path.dirname(_PAIRS_JSON_PATH)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def _load_pairs_json() -> Optional[List[PairEntry]]:
    if not os.path.exists(_PAIRS_JSON_PATH):
        return None
    try:
        with open(_PAIRS_JSON_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        out: List[PairEntry] = []
        for item in raw:
            ex = str(item.get("exchange", "gate")).strip().lower()
            pr = str(item.get("pair", "")).strip().upper()
            if ex and pr:
                out.append(PairEntry(exchange=ex, pair=pr))
        return out
    except Exception:
        # Игнорируем битый JSON — дадим fallback
        return None


def _save_pairs_json(items: List[PairEntry]) -> None:
    _ensure_data_dir()
    with open(_PAIRS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump([asdict(x) for x in items], f, ensure_ascii=False, indent=2)


def _validate_exchanges(items: List[PairEntry]) -> List[PairEntry]:
    allowed = set(exchange_proxy.available_exchanges())
    return [x for x in items if x.exchange in allowed]


def _dedupe(items: List[PairEntry]) -> List[PairEntry]:
    seen = set()
    out: List[PairEntry] = []
    for x in items:
        k = x.key()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


# ========= Публичное API =========

def list_pairs() -> List[Dict[str, Any]]:
    """
    Нормализованный список пар:
    [{"exchange": "gate", "pair": "EDGE_USDT"}, ...]
    Приоритет источников:
      1) data/pairs.json (если есть и валиден)
      2) config.PAIR (старый стиль, одна пара => EXCHANGE=gate)
    """
    # 1) JSON из админки / ручного редактирования
    items = _load_pairs_json()
    if items is None:
        # 2) fallback: одиночная пара из config.PAIR
        pr = (PAIR or "").strip().upper()
        items = [PairEntry(exchange="gate", pair=pr)] if pr else []

    items = _validate_exchanges(items)
    items = _dedupe(items)
    return [asdict(x) for x in items]


def upsert_pairs(new_pairs: List[Dict[str, Any]]) -> None:
    """
    Полная замена списка пар (идемпотентная).
    Ожидаемый формат:
      [{"exchange": "gate", "pair": "EDGE_USDT"}, {"exchange": "htx", "pair": "HT_USDT"}, ...]
    - Валидируем exchange по реестру бирж;
    - pair -> UPPER;
    - Дедуп;
    - Сохраняем в data/pairs.json.
    """
    if not isinstance(new_pairs, list):
        raise ValueError("upsert_pairs expects a list of dicts")

    items: List[PairEntry] = []
    for obj in new_pairs:
        if not isinstance(obj, dict):
            continue
        ex = str(obj.get("exchange", "gate")).strip().lower() or "gate"
        pr = str(obj.get("pair", "")).strip().upper()
        if not pr:
            continue
        items.append(PairEntry(exchange=ex, pair=pr))

    items = _validate_exchanges(items)
    items = _dedupe(items)

    _save_pairs_json(items)
