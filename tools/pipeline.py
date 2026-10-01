"""논문 처리 단계: 분석 → 해설 → 번역(단위마다) · Q&A · 버튼 작업(발표 요약, 코드, 비교, 계보도, 라이브러리 질문).

모든 함수는 progress(i, n, message) 콜백을 받을 수 있고, 결과는 library/<key>/ 에 바로 저장합니다.
화면(서버)과 CLI(rp.py) 가 같은 함수를 부릅니다.
"""
import json
import re
import uuid

from . import claude, config, exporter, library, prompts
from .utils import LOCK, append_jsonl, load_json, now_str, read_jsonl, save_json, write_text, write_jsonl


def _noop(*_a):
    pass


def _rel(path):
    return path.relative_to(config.ROOT).as_posix()


# ---------- 1. 분석 ----------
def analyze(key, progress=_noop):
    m = library.load_meta(key)
    progress(0, 2, "원문 추출 중")
    pages = library.ensure_pages(key)
    text = "\n\n".join("[p.{}]\n{}".format(i + 1, t) for i, t in enumerate(pages)) if pages else None
    progress(1, 2, "Claude 가 논문 구조를 읽는 중")
    res = claude.run(prompts.analyze(m, text, library.rel_pdf(m)), "analyze", prompts.ANALYZE_SCHEMA,
                     tools=() if text else ("Read",), key=key)
    d = res["data"]
    units, seen = [], set()
    for u in d.get("units", []):
        uid = re.sub(r"[^A-Za-z0-9.]+", "", u.get("id") or "") or "u{}".format(len(units))
        while uid in seen:
            uid += "b"
        seen.add(uid)
        units.append({"id": uid, "title": u.get("title", ""), "kind": u.get("kind", "body"),
                      "page_start": int(u.get("page_start") or 1), "page_end": int(u.get("page_end") or u.get("page_start") or 1),
                      "anchor": u.get("anchor", ""), "status": "todo"})
    with LOCK:
        m = library.load_meta(key)
        old = {u["id"]: u for u in m.get("units", [])}
        for u in units:   # 다시 분석해도 이미 번역한 단위는 살립니다
            if old.get(u["id"], {}).get("status") == "done" and library.unit_file(key, units.index(u)).exists():
                u["status"] = "done"
        m.update({k: d.get(k) for k in ("title", "short_name", "authors", "venue", "year", "arxiv_id", "code_url",
                                        "task", "keywords", "abstract_ko")})
        m["units"], m["status"] = units, "analyzed"
        if pages:
            m["num_pages"] = len(pages)
        library.save_meta(m)
    library.merge_glossary(key, d.get("glossary", []))
    progress(2, 2, "분석 완료")
    exporter.export(key)
    return m


# ---------- 2. 번역 ----------
def translate(key, progress=_noop, only=None, include_appendix=True, stop=None):
    """번역 안 된 단위를 순서대로 번역합니다. only = 다시 번역할 단위 번호 목록."""
    m = library.load_meta(key)
    pages = library.ensure_pages(key)
    units = m["units"]
    todo = []
    for i, u in enumerate(units):
        if u["kind"] == "references" or (u["kind"] == "appendix" and not include_appendix):
            continue
        if only is not None:
            if i in only:
                todo.append(i)
        elif u.get("status") != "done":
            todo.append(i)
    # 단위 몇 개를 동시에 번역합니다 (config.TRANSLATE_PARALLEL). 끝나는 대로 다음 단위를 넣고, 멈추기를 누르면 새 단위는 넣지 않습니다.
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
    par = max(1, int(config.TRANSLATE_PARALLEL))
    queue_ = list(todo)
    running, done_n, fails, last_err = {}, 0, 0, None
    with ThreadPoolExecutor(max_workers=par) as ex:
        while queue_ or running:
            while queue_ and len(running) < par and not (stop and stop()) and fails < 2:
                i = queue_.pop(0)
                _set_unit_status(key, i, "running")
                running[ex.submit(translate_unit, key, i, pages)] = i
            if not running:
                break
            names = ", ".join(units[i]["id"] for i in running.values())
            progress(done_n, len(todo), "번역 중: " + names)
            finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for f in finished:
                i = running.pop(f)
                done_n += 1
                try:
                    f.result()
                    fails = 0
                except claude.ClaudeError as e:
                    _set_unit_status(key, i, "error", str(e)[:300])
                    fails, last_err = fails + 1, e
    if fails >= 2 and last_err:
        for i in queue_:
            _set_unit_status(key, i, "todo")
        raise last_err
    if stop and stop() and queue_:
        progress(done_n, len(todo), "멈춤")
        exporter.export(key)
        return
    m = library.load_meta(key)
    done, total = library.progress(m)
    if done == total:
        library.update_meta(key, status="translated")
    progress(len(todo), len(todo), "번역 완료")
    exporter.export(key)


def estimate(key):
    """남은 번역의 예상 시간·토큰 (지금까지의 번역 기록 평균, 기록이 없으면 단위당 50초 · 입력 30K · 출력 4K 토큰)."""
    m = library.load_meta(key) or {}
    left = sum(1 for u in m.get("units", []) if u.get("kind") != "references" and u.get("status") != "done")
    logs = [x for x in read_jsonl(claude.LOG_FILE) if x.get("kind") == "translate" and x.get("ok")][-40:]
    sec = sum(x["sec"] for x in logs) / len(logs) if logs else 50.0
    tl = [x for x in logs if x.get("tin")]
    tin = sum(x["tin"] for x in tl) / len(tl) if tl else 30000
    tout = sum(x["tout"] for x in tl) / len(tl) if tl else 4000
    par = max(1, int(config.TRANSLATE_PARALLEL))
    return {"left": left, "minutes": int(round(left * sec / par / 60.0 + 0.49)), "tin": int(left * tin), "tout": int(left * tout)}


def _set_unit_status(key, i, status, err=""):
    with LOCK:
        m = library.load_meta(key)
        m["units"][i]["status"] = status
        m["units"][i]["error"] = err
        library.save_meta(m)


def translate_unit(key, i, pages=None):
    m = library.load_meta(key)
    units = m["units"]
    u = units[i]
    nxt = units[i + 1] if i + 1 < len(units) else None
    if pages:
        from .pdf import slice_unit
        src, exact = slice_unit(pages, u["page_start"], u["page_end"], u.get("anchor"),
                                nxt.get("anchor") if nxt else None, nxt["page_start"] if nxt else None)
    else:
        src, exact = "(원문 추출 실패 — PDF 의 {}~{}쪽을 Read 로 직접 읽으세요)".format(u["page_start"], u["page_end"]), False
    gl = library.load_glossary(key) + _global_terms()
    res = claude.run(prompts.translate(m, u, src, exact, library.rel_pdf(m), gl), "translate", prompts.TRANSLATE_SCHEMA,
                     tools=("Read",), key=key)
    blocks = []
    for n, b in enumerate(res["data"].get("blocks", []), 1):
        b = {k: v for k, v in b.items() if v not in ("", None) or k in ("orig", "ko")}
        b["id"] = "{}-{}".format(u["id"], n)
        blocks.append(b)
    save_json(library.unit_file(key, i), {"unit": u["id"], "title": u["title"], "blocks": blocks, "translated": now_str(),
                                          "model": config.MODELS["translate"]})
    library.merge_glossary(key, res["data"].get("terms", []))
    _set_unit_status(key, i, "done")


def _global_terms():
    return load_json(config.DATA / "glossary.json", []) or []


# ---------- 3. 해설 ----------
def overview(key, progress=_noop):
    m = library.load_meta(key)
    pages = library.ensure_pages(key)
    text = "\n\n".join("[p.{}]\n{}".format(i + 1, t) for i, t in enumerate(pages)) if pages else "(원문 추출 실패 — PDF 를 Read 로 읽으세요)"
    progress(0, 1, "Claude 가 해설을 쓰는 중 (관련 논문 확인 포함, 몇 분 걸립니다)")
    res = claude.run(prompts.overview(m, text, library.rel_pdf(m)), "overview", prompts.OVERVIEW_SCHEMA,
                     tools=("Read", "WebSearch", "WebFetch"), key=key)
    d = res["data"]
    d["created"], d["model"] = now_str(), config.MODELS["overview"]
    save_json(library.paper_dir(key) / "overview.json", d)
    progress(1, 1, "해설 완료")
    exporter.export(key)
    return d


# ---------- 4. Q&A ----------
def anchor_info(key, anchor):
    """anchor: "" / "all" = 논문 전체, 블록 id (예: "3.2-4") = 그 블록."""
    if not anchor or anchor == "all":
        return "논문 전체", "", "전체"
    b = library.find_block(key, anchor)
    if not b:
        return "논문 전체", "", "전체"
    if b["type"] == "equation":
        text = "$$ {} $$ {}\n(설명: {})".format(b.get("latex", ""), b.get("label", ""), b.get("ko", ""))
        tag = "Eq. " + b["label"].strip("() ") if b.get("label") else "§" + anchor
    elif b["type"] in ("figure", "table"):
        text = "{} 캡션: {}\n(번역: {})\n→ PDF {}쪽".format(b.get("label", ""), b.get("orig", ""), b.get("ko", ""), b.get("page"))
        tag = b.get("label") or "§" + anchor
    else:
        text = "원문: {}\n번역: {}".format(b.get("orig", ""), b.get("ko", ""))
        tag = "§" + anchor
    desc = "§{} {} ({}, PDF {}쪽)".format(b["unit"], b["unit_title"], tag, b.get("page"))
    return desc, text, tag


def ask(key, question, anchor="", kind="qa", new_session=False, progress=_noop, length="normal", model=None):
    """질문 하나. model = None(기본 Opus) / "sonnet"(절약). length = short / normal / long.

    토큰 절약: 같은 논문의 질문은 세션을 이어 가지만, 세션 입력이 config.QA_SESSION_LIMIT 를 넘으면
    다음 질문은 앞 대화 전체 대신 지난 Q&A 요약(제목·핵심)만 들고 새 세션으로 시작합니다.
    모델을 바꾸면 세션도 새로 시작합니다 (다른 모델로는 이어 갈 수 없음)."""
    m = library.load_meta(key)
    desc, text, tag = anchor_info(key, anchor)
    prompt = prompts.ask(kind, question, desc, text, length)
    model = model or config.MODELS["qa"]
    sid = m.get("qa_session")
    compacted = False
    if new_session or m.get("qa_model", config.MODELS["qa"]) != model:
        sid = None
    elif sid and (m.get("qa_session_tin") or 0) > config.QA_SESSION_LIMIT:
        sid, compacted = None, True
    tools = ("Read", "Grep", "WebSearch", "WebFetch")

    def on_text(t):
        progress(None, None, "답을 쓰는 중", partial=t)
    progress(0, 1, "Claude 가 논문을 확인하는 중")
    res = None
    if sid:
        try:
            res = claude.run(prompt, "qa", prompts.QA_SCHEMA, tools=tools, resume=sid, key=key, on_text=on_text, model=model)
        except claude.ClaudeError as e:
            if "conversation" not in str(e).lower() and "session" not in str(e).lower():
                raise
    if res is None:
        sid = claude.new_session_id()
        library.ensure_pages(key)
        primer = prompts.session_primer(m, library.rel_pdf(m), "library/{}/paper.txt".format(key), m.get("units", []))
        digest = prompts.history_digest(library.load_qa(key)) if (compacted or not new_session) else ""
        res = claude.run(primer + digest + prompt, "qa", prompts.QA_SCHEMA, tools=tools, session_id=sid, key=key,
                         on_text=on_text, model=model)
    library.update_meta(key, qa_session=res.get("session_id") or sid, qa_session_tin=res.get("tin", 0), qa_model=model)
    d = res["data"]
    item = {"id": uuid.uuid4().hex[:10], "ts": now_str(), "kind": kind, "anchor": anchor or "all", "tag": tag,
            "question": question, "title": d.get("title", ""), "tags": d.get("tags", []), "answer": d.get("answer", ""),
            "key_point": d.get("key_point", ""), "model": model, "length": length}
    append_jsonl(library.paper_dir(key) / "qa.jsonl", item)
    exporter.export(key)
    return item


def delete_qa(key, qid):
    p = library.paper_dir(key) / "qa.jsonl"
    items = [q for q in read_jsonl(p) if q.get("id") != qid]
    write_jsonl(p, items)
    exporter.export(key)


# ---------- 5. 메모 · 하이라이트 · 용어집 ----------
def set_note(key, block_id, memo=None, add_hl=None, remove_hl=None):
    """메모 · 하이라이트. 하이라이트는 드래그로 고른 글자: {"f": "ko" 또는 "orig", "s": "고른 글"}."""
    with LOCK:
        p = library.paper_dir(key) / "notes.json"
        notes = load_json(p, {}) or {}
        n = notes.get(block_id, {})
        n.pop("hl", None)   # 예전 방식(문단 통째 하이라이트)은 정리
        if memo is not None:
            n["memo"] = memo.strip()
        hls = [h for h in n.get("hls", []) if h.get("s")]
        if add_hl and (add_hl.get("s") or "").strip():
            h = {"f": add_hl.get("f", "ko"), "s": add_hl["s"].strip()}
            if h not in hls:
                hls.append(h)
        if remove_hl:
            hls = [h for h in hls if not (h["f"] == remove_hl.get("f") and h["s"] == remove_hl.get("s"))]
        n["hls"] = hls
        n["ts"] = now_str()
        if not n.get("memo") and not n.get("hls"):
            notes.pop(block_id, None)
        else:
            notes[block_id] = n
        save_json(p, notes)
    exporter.export(key)
    return notes


def save_glossary(key, terms, to_global=None):
    out = library.save_glossary(key, terms)
    if to_global:
        with LOCK:
            g = _global_terms()
            have = {t["en"].lower(): t for t in g}
            for t in to_global:
                have[t["en"].lower()] = {"en": t["en"], "ko": t.get("ko", ""), "note": t.get("note", "")}
            save_json(config.DATA / "glossary.json", sorted(have.values(), key=lambda t: t["en"].lower()))
    return out


# ---------- 6. 버튼 작업 ----------
def slides(key, progress=_noop):
    m = library.load_meta(key)
    library.ensure_pages(key)
    progress(0, 1, "발표 요약 만드는 중")
    res = claude.run(prompts.slides(m, library.load_overview(key), "library/{}/paper.txt".format(key), library.rel_pdf(m)),
                     "slides", prompts.SLIDES_SCHEMA, tools=("Read",), key=key)
    write_text(library.paper_dir(key) / "slides.md", res["data"]["markdown"])
    progress(1, 1, "완료")
    exporter.export(key)


def code(key, progress=_noop):
    m = library.load_meta(key)
    library.ensure_pages(key)
    progress(0, 1, "코드 저장소 찾는 중")
    res = claude.run(prompts.code(m, library.load_overview(key), "library/{}/paper.txt".format(key)),
                     "code", prompts.CODE_SCHEMA, tools=("Read", "WebSearch", "WebFetch"), key=key)
    d = res["data"]
    d["created"] = now_str()
    save_json(library.paper_dir(key) / "code.json", d)
    progress(1, 1, "완료")
    exporter.export(key)


def _blob(key, full=True):
    m = library.load_meta(key)
    ov = library.load_overview(key) or {}
    head = {k: m.get(k) for k in ("short_name", "authors", "venue", "year", "task", "keywords", "abstract_ko")}
    if full:
        body = {k: ov.get(k) for k in ("one_liner", "problem", "key_idea", "method_flow", "task_position", "results", "limitations")}
    else:
        body = {k: ov.get(k) for k in ("one_liner", "task_position", "timeline", "related")}
    return {"key": key, "title": m.get("title"), "blob": json.dumps({"meta": head, "overview": body}, ensure_ascii=False)}


def compare(keys, progress=_noop):
    progress(0, 1, "비교표 만드는 중")
    res = claude.run(prompts.compare([_blob(k) for k in keys]), "compare", prompts.COMPARE_SCHEMA, tools=("Read",))
    d = res["data"]
    d.update({"id": uuid.uuid4().hex[:8], "keys": keys, "created": now_str()})
    p = config.DATA / "comparisons.json"
    with LOCK:
        items = load_json(p, []) or []
        items.insert(0, d)
        save_json(p, items)
    progress(1, 1, "완료")
    return d


def lineage(progress=_noop):
    keys = [k for k in library.all_keys() if library.load_overview(k)]
    if not keys:
        raise claude.ClaudeError("해설이 만들어진 논문이 없습니다. 논문을 하나 이상 분석해 주세요.")
    progress(0, 1, "계보도 만드는 중 ({}편)".format(len(keys)))
    res = claude.run(prompts.lineage([_blob(k, full=False) for k in keys]), "lineage", prompts.LINEAGE_SCHEMA,
                     tools=("Read", "WebSearch", "WebFetch"))
    d = res["data"]
    d["created"], d["keys"] = now_str(), keys
    save_json(config.DATA / "lineage.json", d)
    progress(1, 1, "완료")
    return d


def global_ask(question, progress=_noop):
    rows = []
    for k in library.all_keys():
        m = library.load_meta(k)
        ov = library.load_overview(k) or {}
        rows.append("- [{}] {} ({} {}) — {}".format(k, m.get("title"), m.get("venue", ""), m.get("year", ""), ov.get("one_liner", "")))
    progress(0, 1, "라이브러리에서 찾는 중")
    res = claude.run(prompts.global_ask(question, "\n".join(rows)), "global", prompts.GLOBAL_SCHEMA, tools=("Read", "Grep", "Glob"),
                     on_text=lambda t: progress(None, None, "답을 쓰는 중", partial=t))
    d = res["data"]
    item = {"id": uuid.uuid4().hex[:10], "ts": now_str(), "question": question, "title": d.get("title", ""),
            "answer": d.get("answer", ""), "papers": d.get("papers", [])}
    append_jsonl(config.DATA / "global_qa.jsonl", item)
    progress(1, 1, "완료")
    return item


# ---------- 7. 한 번에 시작 ----------
def start(key, progress=_noop):
    """새 논문: 분석 → (해설과 번역은 작업 큐가 따로 이어서 돌림)"""
    return analyze(key, progress)
