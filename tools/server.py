"""로컬 서버 (표준 라이브러리 http.server). 화면(web/)을 보여 주고, /api/* 로 데이터와 작업을 주고받습니다.

보안
- 127.0.0.1 에서만 열리므로 이 컴퓨터 밖에서는 접근할 수 없습니다.
- 이 컴퓨터에서 열린 다른 웹페이지가 몰래 요청을 보내지 못하도록:
  · 서버를 켤 때마다 비밀 토큰을 새로 만들어 화면(index.html)에만 심고, /api · /pdf · /fig 요청은 그 토큰이 있어야 받습니다
    (화면은 X-RP-Token 헤더, PDF 보기 창처럼 헤더를 못 붙이는 곳은 ?t= 로 보냄)
  · Host 가 127.0.0.1/localhost:포트 가 아니면 거절 (DNS 리바인딩 방어), Origin 이 있으면 같은 주소여야 함
"""
import base64
import hmac
import json
import mimetypes
import os
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import arxiv, claude, config, exporter, jobs, library, pipeline, search
from .utils import load_json, read_jsonl

LAST_PING = [time.time()]
ROUTES = []
TOKEN = secrets.token_urlsafe(24)
PORT = [config.PORT]


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


KEY_RE = re.compile(r"^[A-Za-z0-9\-]+$")
BLOCK_RE = re.compile(r"^[A-Za-z0-9.]+-\d+$")


def _fig_path(key, block):
    """그림 캐시 파일 경로 (key·블록 id 모양을 엄격히 검사 → 폴더 밖으로 못 나감)."""
    if not KEY_RE.match(key) or not BLOCK_RE.match(block) or ".." in block:
        return None
    return library.paper_dir(key) / "figs" / (block + ".png")


def _key(key):
    if not KEY_RE.match(key) or not library.meta_path(key).exists():
        raise ApiError("논문을 찾지 못했습니다: " + key, 404)
    return key


# ---------- 읽기 ----------
@route("GET", r"/api/ping")
def ping(h, q):
    LAST_PING[0] = time.time()
    return {"ok": True}


@route("GET", r"/api/library")
def get_library(h, q):
    library.scan()
    return {"papers": [library.summary(k) for k in library.all_keys()], "folders": library.load_folders()["folders"],
            "usage": claude.usage(), "limits": claude.limits(),
            "notion": bool(config.NOTION_TOKEN), "claude": bool(claude.find_claude())}


@route("GET", r"/api/paper/([^/]+)")
def get_paper(h, q, key):
    _key(key)
    m = library.load_meta(key)
    d = library.paper_dir(key)
    slides = d / "slides.md"
    return {"meta": m, "overview": library.load_overview(key), "qa": exporter.qa_sorted(key, q.get("order", "section")),
            "notes": library.load_notes(key), "glossary": library.load_glossary(key),
            "slides": slides.read_text(encoding="utf-8") if slides.exists() else "",
            "code": load_json(d / "code.json", None), "busy": jobs.busy(key), "usage": claude.usage(key),
            "progress": library.progress(m), "estimate": pipeline.estimate(key),
            "notion": (load_json(config.DATA / "notion.json", {}) or {}).get("pages", {}).get(key)}


@route("GET", r"/api/paper/([^/]+)/blocks")
def get_blocks(h, q, key):
    _key(key)
    m = library.load_meta(key)
    figdir = library.paper_dir(key) / "figs"
    cached = {p.stem for p in figdir.glob("*.png")} if figdir.exists() else set()
    units = []
    for i, u in enumerate(m.get("units", [])):
        d = library.load_unit(key, i)
        blocks = [dict(b, fig=b["id"] in cached) if b.get("type") in ("figure", "table") else b for b in (d or {}).get("blocks", [])]
        units.append(dict(u, i=i, blocks=blocks))
    return {"units": units}


@route("POST", r"/api/paper/([^/]+)/figure")
def post_figure(h, q, body, key):
    """화면이 PDF.js 로 잘라 낸 그림을 캐시합니다 (다음부터 바로 보이고, Notion 에도 올라감)."""
    _key(key)
    f = _fig_path(key, body.get("block", ""))
    data = body.get("png", "")
    if not f or not data.startswith("data:image/png;base64,"):
        raise ApiError("그림 형식이 맞지 않습니다.")
    raw = base64.b64decode(data.split(",", 1)[1])
    if len(raw) > 8 * 1024 * 1024 or not raw.startswith(b"\x89PNG"):
        raise ApiError("그림이 너무 크거나 PNG 가 아닙니다.")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(raw)
    return {"ok": True}


@route("GET", r"/api/jobs")
def get_jobs(h, q):
    return {"jobs": jobs.listing(), "usage": claude.usage(), "limits": claude.limits()}


@route("GET", r"/api/search")
def get_search(h, q):
    return {"results": search.search(q.get("q", ""))}


@route("GET", r"/api/comparisons")
def get_comparisons(h, q):
    return {"items": load_json(config.DATA / "comparisons.json", []) or []}


@route("GET", r"/api/global-qa")
def get_global_qa(h, q):
    return {"items": list(reversed(read_jsonl(config.DATA / "global_qa.jsonl")))}


@route("GET", r"/api/glossary")
def get_global_glossary(h, q):
    return {"terms": load_json(config.DATA / "glossary.json", []) or []}


# ---------- 작업 ----------
def _job(kind, key, label, fn, lane="bg", then=None, unique=True):
    return {"job": jobs.submit(kind, label, fn, key=key, lane=lane, then=then, unique=unique)}


def _name(key):
    m = library.load_meta(key)
    return m.get("short_name") or m.get("title") or key


@route("POST", r"/api/scan")
def post_scan(h, q, body):
    return {"new": library.scan()}


@route("POST", r"/api/arxiv")
def post_arxiv(h, q, body):
    text = body.get("text", "")

    def fn(progress, stop):
        try:
            r = arxiv.add(text, progress)
        except arxiv.ArxivError as e:
            raise ApiError(str(e))
        if r["key"] and body.get("start", True):
            _start(r["key"])
        return r
    return _job("arxiv", None, "arXiv 추가: " + text[:40], fn, unique=False)


def _start(key):
    """분석이 끝나면 해설과 번역을 동시에 시작합니다."""
    def after(job):
        if job["status"] == "done":
            _overview(key)
            _translate(key)
    return jobs.submit("analyze", "분석 · " + _name(key), lambda p, s: pipeline.analyze(key, p) and None, key=key, then=[after])


def _overview(key):
    return jobs.submit("overview", "해설 · " + _name(key), lambda p, s: pipeline.overview(key, p) and None, key=key)


def _translate(key, only=None, appendix=True):
    # 단위 하나만 다시 번역하는 요청은 진행 중인 전체 번역과 따로 돕니다
    label = "번역 · " + _name(key) if only is None else "다시 번역 · {} ({}개 단위)".format(_name(key), len(only))
    return jobs.submit("translate" if only is None else "retranslate", label,
                       lambda p, s: pipeline.translate(key, p, only=only, include_appendix=appendix, stop=s), key=key,
                       unique=only is None)


@route("POST", r"/api/paper/([^/]+)/start")
def post_start(h, q, body, key):
    _key(key)
    return {"job": _start(key)}


@route("POST", r"/api/paper/([^/]+)/analyze")
def post_analyze(h, q, body, key):
    _key(key)
    return _job("analyze", key, "분석 · " + _name(key), lambda p, s: pipeline.analyze(key, p) and None)


@route("POST", r"/api/paper/([^/]+)/overview")
def post_overview(h, q, body, key):
    _key(key)
    return {"job": _overview(key)}


@route("POST", r"/api/paper/([^/]+)/translate")
def post_translate(h, q, body, key):
    _key(key)
    return {"job": _translate(key, body.get("units"), body.get("appendix", True))}


@route("POST", r"/api/paper/([^/]+)/ask")
def post_ask(h, q, body, key):
    _key(key)
    question = (body.get("question") or "").strip()
    kind = body.get("kind", "qa")
    if kind == "qa" and not question:
        raise ApiError("질문을 입력해 주세요.")
    anchor = body.get("anchor") or "all"
    label = {"qa": "질문", "explain": "깊게 해설", "figure": "그림 해설", "equation": "수식 풀이"}.get(kind, "질문")
    return _job("ask", key, "{} · {}".format(label, anchor if anchor != "all" else "전체"),
                lambda p, s: pipeline.ask(key, question, anchor, kind, body.get("new_session", False), progress=p,
                                          length=body.get("length", "normal"), model=body.get("model") or None),
                lane="fg", unique=False)


@route("POST", r"/api/paper/([^/]+)/qa/delete")
def post_qa_delete(h, q, body, key):
    pipeline.delete_qa(_key(key), body["id"])
    return {"ok": True}


@route("POST", r"/api/paper/([^/]+)/note")
def post_note(h, q, body, key):
    return {"notes": pipeline.set_note(_key(key), body["block"], body.get("memo"), body.get("add_hl"), body.get("remove_hl"))}


@route("POST", r"/api/paper/([^/]+)/glossary")
def post_glossary(h, q, body, key):
    return {"terms": pipeline.save_glossary(_key(key), body.get("terms", []), body.get("to_global"))}


@route("POST", r"/api/paper/([^/]+)/reading")
def post_reading(h, q, body, key):
    library.update_meta(_key(key), reading=body.get("state", "todo"))
    return {"ok": True}


@route("POST", r"/api/paper/([^/]+)/reset-session")
def post_reset(h, q, body, key):
    library.update_meta(_key(key), qa_session=None)
    return {"ok": True}


@route("POST", r"/api/paper/([^/]+)/slides")
def post_slides(h, q, body, key):
    _key(key)
    return _job("slides", key, "발표 요약 · " + _name(key), lambda p, s: pipeline.slides(key, p))


@route("POST", r"/api/paper/([^/]+)/code")
def post_code(h, q, body, key):
    _key(key)
    url = (body.get("repo_url") or "").strip()
    if url and not re.match(r"^https?://\S+$", url):
        raise ApiError("저장소 주소는 https:// 로 시작해야 합니다.")
    return _job("code", key, "코드 연결 · " + _name(key), lambda p, s: pipeline.code(key, p, url or None))


@route("POST", r"/api/paper/([^/]+)/notion")
def post_notion(h, q, body, key):
    _key(key)
    from . import notion
    return _job("notion", key, "Notion · " + _name(key), lambda p, s: notion.sync(key, p))


@route("POST", r"/api/paper/([^/]+)/open")
def post_open(h, q, body, key):
    m = library.load_meta(_key(key))
    what = body.get("what", "pdf")
    if what == "pdf":
        os.startfile(str(library.pdf_path(m)))  # noqa: S606 — 이 컴퓨터의 기본 PDF 프로그램으로 열기
    else:
        os.startfile(str(exporter.export(key)))  # noqa: S606
    return {"ok": True}


@route("POST", r"/api/folders")
def post_folders(h, q, body):
    if body.get("key"):
        _key(body["key"])
    try:
        d = library.folder_action(body)
    except ValueError as e:
        raise ApiError(str(e))
    return {"folders": d["folders"]}


@route("POST", r"/api/comparisons/delete")
def post_compare_delete(h, q, body):
    from .utils import LOCK, save_json
    with LOCK:
        items = [c for c in (load_json(config.DATA / "comparisons.json", []) or []) if c.get("id") != body.get("id")]
        save_json(config.DATA / "comparisons.json", items)
    return {"ok": True}


@route("POST", r"/api/global-qa/delete")
def post_global_delete(h, q, body):
    from .utils import write_jsonl
    p = config.DATA / "global_qa.jsonl"
    write_jsonl(p, [x for x in read_jsonl(p) if x.get("id") != body.get("id")])
    return {"ok": True}


@route("POST", r"/api/compare")
def post_compare(h, q, body):
    keys = body.get("keys", [])
    if len(keys) < 2:
        raise ApiError("비교할 논문을 두 편 이상 골라 주세요.")
    return _job("compare", None, "비교표 · {}편".format(len(keys)), lambda p, s: pipeline.compare(keys, p), unique=False)


@route("POST", r"/api/global-ask")
def post_global_ask(h, q, body):
    question = (body.get("question") or "").strip()
    if not question:
        raise ApiError("질문을 입력해 주세요.")
    return _job("global", None, "라이브러리 질문", lambda p, s: pipeline.global_ask(question, p), lane="fg", unique=False)


@route("POST", r"/api/jobs/([^/]+)/stop")
def post_stop(h, q, body, jid):
    return {"ok": jobs.stop(jid)}


@route("GET", r"/api/jobs/([^/]+)")
def get_job(h, q, jid):
    j = jobs.get(jid)
    if not j:
        raise ApiError("작업을 찾지 못했습니다.", 404)
    return {"job": j}


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        extra = dict(extra or {})
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", extra.pop("Cache-Control", "no-store"))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, data):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _allowed_host(self):
        hosts = {"127.0.0.1:{}".format(PORT[0]), "localhost:{}".format(PORT[0])}
        if (self.headers.get("Host") or "").lower() not in hosts:
            return False
        origin = self.headers.get("Origin")
        return not origin or origin.lower() in {"http://" + h for h in hosts}

    def _token_ok(self, q):
        got = self.headers.get("X-RP-Token") or q.get("t") or ""
        return hmac.compare_digest(got.encode(), TOKEN.encode())

    def _dispatch(self, method):
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        if not self._allowed_host():
            return self._json(403, {"error": "허용되지 않은 주소입니다."})
        protected = path.startswith(("/api/", "/pdf/", "/fig/"))
        if protected and not self._token_ok(q):
            return self._json(403, {"error": "인증 토큰이 맞지 않습니다. 창을 새로 고침해 주세요."})
        if method == "GET" and not path.startswith("/api/"):
            return self._static(path)
        for m, rx, fn in ROUTES:
            if m != method:
                continue
            mt = rx.match(path)
            if not mt:
                continue
            try:
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
                    out = fn(self, q, body, *mt.groups())
                else:
                    out = fn(self, q, *mt.groups())
                return self._json(200, out)
            except ApiError as e:
                return self._json(e.code, {"error": str(e)})
            except claude.ClaudeError as e:
                return self._json(500, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"error": "{}: {}".format(e.__class__.__name__, e)})
        self._json(404, {"error": "없는 주소: " + path})

    def _static(self, path):
        if path.startswith("/pdf/"):
            key = path[5:]
            m = library.load_meta(key) if KEY_RE.match(key) else None
            return self._file(library.pdf_path(m), "application/pdf") if m else self._json(404, {"error": "no pdf"})
        if path.startswith("/fig/"):
            mt = re.match(r"^/fig/([A-Za-z0-9\-]+)/([A-Za-z0-9.\-]+)\.png$", path)
            f = _fig_path(mt.group(1), mt.group(2)) if mt else None
            if not f or not f.is_file():
                return self._json(404, {"error": "no figure"})
            return self._file(f, "image/png", cache=True)
        if path in ("/", ""):
            path = "/index.html"
        f = (config.WEB / path.lstrip("/")).resolve()
        if config.WEB.resolve() not in f.parents or not f.is_file():
            return self._json(404, {"error": "not found"})
        if f.name == "index.html":   # 비밀 토큰을 화면에 심어서 보냄
            body = f.read_text(encoding="utf-8").replace("__RP_TOKEN__", TOKEN).encode("utf-8")
            return self._send(200, body, "text/html; charset=utf-8")
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self._file(f, ctype)

    def _file(self, f, ctype, cache=False):
        """PDF 보기 창이 쪽을 나눠 받을 수 있게 Range 요청을 지원합니다. cache = 브라우저가 하루 동안 다시 받지 않음 (그림)."""
        data = f.read_bytes()
        cc = {"Cache-Control": "private, max-age=86400"} if cache else {}
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
        if m and (m.group(1) or m.group(2)):
            size = len(data)
            if m.group(1):
                s, e = int(m.group(1)), int(m.group(2)) if m.group(2) else size - 1
            else:
                s, e = size - int(m.group(2)), size - 1
            e = min(e, size - 1)
            return self._send(206, data[s:e + 1], ctype, dict(cc, **{"Content-Range": "bytes {}-{}/{}".format(s, e, size), "Accept-Ranges": "bytes"}))
        self._send(200, data, ctype, dict(cc, **{"Accept-Ranges": "bytes"}))

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def make_server(port=None):
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    srv = ThreadingHTTPServer((config.HOST, port or config.PORT), Handler)
    srv.daemon_threads = True
    PORT[0] = srv.server_address[1]
    jobs.start()
    library.scan()
    return srv


def serve(port=None):
    srv = make_server(port)
    print("Paper Reader: http://{}:{}/  (Ctrl+C 로 종료)".format(config.HOST, srv.server_address[1]))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def serve_in_thread(port=None):
    srv = make_server(port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
