import time, json, hashlib, hmac
from urllib.parse import urlencode
from typing import Dict, Any
from config import API_SECRET, PREFIX

def _hmac_sign(method: str, path_with_prefix: str, query: str, body: str, timestamp: str) -> str:
    body_hash = hashlib.sha512(body.encode("utf-8")).hexdigest()
    raw = f"{method}\n{path_with_prefix}\n{query}\n{body_hash}\n{timestamp}"
    return hmac.new(API_SECRET.encode("utf-8"), raw.encode("utf-8"), hashlib.sha512).hexdigest()

def headers_signed(method: str, path: str, query: Dict[str, Any] | None, body_obj: Dict[str, Any] | None):
    ts = str(int(time.time()))
    q = "" if not query else urlencode(query, doseq=True)
    body = "" if not body_obj else json.dumps(body_obj, separators=(",", ":"))
    sign = _hmac_sign(method.upper(), PREFIX + path, q, body, ts)
    headers = {
        "KEY": "",         # заполнится в http.request
        "Timestamp": ts,
        "SIGN": sign,
        "Content-Type": "application/json",
    }
    return headers, q, body
