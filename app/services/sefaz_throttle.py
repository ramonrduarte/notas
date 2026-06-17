import time
import threading

_lock = threading.Lock()
_last: dict[str, float] = {}

# Minimum seconds between consecutive requests to each SEFAZ service.
# NF-e DistDFe is the strictest — 10 s prevents cStat 656 even during heavy recovery scans.
_INTERVALS: dict[str, float] = {
    "nfe": 10.0,
    "cte": 5.0,
}


def throttle(service: str) -> None:
    """Block until it is safe to send another request to the given SEFAZ service.

    The interval is measured from the START of the previous request, so
    slow responses never shrink the gap between calls.
    """
    interval = _INTERVALS.get(service, 5.0)
    with _lock:
        now = time.time()
        last = _last.get(service, 0.0)
        elapsed = now - last
        wait = max(0.0, interval - elapsed)
        # Reserve the slot optimistically so concurrent callers are staggered.
        _last[service] = now + wait
    if wait > 0:
        time.sleep(wait)
