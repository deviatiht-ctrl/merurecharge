"""Piblisye/sibskrive senp pou Server-Sent Events.

Worker la (thread) ak route yo pibliye; endpoint SSE la konsome.
Frontend la gen fallback polling si SSE tonbe (serverless pa garanti SSE).
"""
import json
import queue
import threading

_lock = threading.Lock()
_subscribers: list[queue.Queue] = []


def publish(event_type: str, data: dict):
    msg = {"type": event_type, "data": data}
    with _lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass


def subscribe() -> queue.Queue:
    q = queue.Queue(maxsize=200)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue):
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def format_sse(msg: dict) -> str:
    return f"event: {msg['type']}\ndata: {json.dumps(msg['data'], default=str)}\n\n"
