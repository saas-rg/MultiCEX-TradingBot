from typing import Dict, Optional

_last_order_id_by_pair: Dict[str, Optional[str]] = {}

def set_last_order_id(pair: str, oid: Optional[str]) -> None:
    _last_order_id_by_pair[pair] = oid

def get_last_order_id(pair: str) -> Optional[str]:
    return _last_order_id_by_pair.get(pair)

def clear_all_orders():
    _last_order_id_by_pair.clear()
