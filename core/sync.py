import time
from config import NEXT_BAR_BUFFER_SEC
from exchanges.gate import get_server_time_epoch

def sleep_until_next_minute(buffer_sec: float | None = None):
    buf = NEXT_BAR_BUFFER_SEC if buffer_sec is None else buffer_sec
    try:
        st = get_server_time_epoch()
    except Exception:
        st = int(time.time())
    sleep_sec = (60 - (st % 60)) + buf
    time.sleep(max(sleep_sec, 0.5))
