"""백그라운드 작업 큐. 화면은 작업을 넣고(/api/...) 진행 상황을 1~2초마다 물어봅니다(/api/jobs).

줄은 두 개입니다.
- bg (일꾼 2명): 분석·해설·번역·발표 요약·코드·비교·Notion — 오래 걸리는 일
- fg (일꾼 1명): Q&A·깊게 해설·라이브러리 질문 — 사용자가 기다리는 일 (번역 중에도 바로 답하도록)
같은 논문의 번역은 한 번에 하나만 돕니다.
"""
import queue
import threading
import time
import traceback
import uuid

from .utils import now_str

_jobs = {}
_lock = threading.Lock()
_queues = {"bg": queue.Queue(), "fg": queue.Queue()}
_started = False


def _worker(q):
    while True:
        job = q.get()
        with _lock:
            if job["status"] == "cancelled":
                continue
            job["status"], job["started"] = "running", now_str()

        def progress(i=None, n=None, msg=None, partial=None, job=job):
            """진행 상황. partial = 지금까지 써진 답 (Q&A 실시간 표시용)"""
            with _lock:
                if i is not None:
                    job["i"] = i
                if n is not None:
                    job["n"] = n
                if msg is not None:
                    job["message"] = msg
                if partial is not None:
                    job["partial"] = partial

        try:
            job["result"] = job.pop("_fn")(progress, lambda job=job: job.get("stop", False))
            with _lock:
                job["status"] = "done"
        except Exception as e:  # noqa: BLE001 — 어떤 오류든 화면에 보여 줍니다
            with _lock:
                job["status"], job["error"] = "error", str(e) or e.__class__.__name__
                job["trace"] = traceback.format_exc()[-2000:]
        finally:
            job["finished"] = now_str()
            job["t1"] = time.time()
            for cb in job.pop("_then", []):
                try:
                    cb(job)
                except Exception:  # noqa: BLE001
                    pass


def start():
    global _started
    if _started:
        return
    _started = True
    for name, n in (("bg", 2), ("fg", 1)):
        for _ in range(n):
            threading.Thread(target=_worker, args=(_queues[name],), daemon=True).start()


def submit(kind, label, fn, key=None, lane="bg", unique=True, then=None):
    """fn(progress, stop) 를 줄에 넣습니다. unique 면 같은 (kind, key) 작업이 이미 대기·진행 중일 때 그 작업을 돌려줍니다."""
    start()
    with _lock:
        if unique:
            for j in _jobs.values():
                if j["kind"] == kind and j["key"] == key and j["status"] in ("queued", "running"):
                    return public(j)
        jid = uuid.uuid4().hex[:10]
        job = {"id": jid, "kind": kind, "key": key, "label": label, "status": "queued", "i": 0, "n": 0, "message": "대기 중",
               "created": now_str(), "t0": time.time(), "_fn": fn, "_then": list(then or [])}
        _jobs[jid] = job
    _queues[lane].put(job)
    return public(job)


def public(j):
    return {k: v for k, v in j.items() if not k.startswith("_") and k != "trace"}


def get(jid):
    with _lock:
        j = _jobs.get(jid)
        return public(j) if j else None


def stop(jid):
    with _lock:
        j = _jobs.get(jid)
        if not j:
            return False
        if j["status"] == "queued":
            j["status"] = "cancelled"
        j["stop"] = True
        return True


def listing(limit=30):
    """진행 중 + 최근 끝난 작업 (끝난 지 10분 넘은 것은 숨김)."""
    now = time.time()
    with _lock:
        items = [public(j) for j in _jobs.values()
                 if j["status"] in ("queued", "running") or now - j.get("t1", now) < 600]
    items.sort(key=lambda j: j["t0"], reverse=True)
    return items[:limit]


def busy(key=None):
    with _lock:
        return [public(j) for j in _jobs.values() if j["status"] in ("queued", "running") and (key is None or j["key"] == key)]
