"""Notion 동기화 (버튼을 눌렀을 때만). Notion 공식 REST API + 통합 토큰 — 무료, Claude 사용량 없음.

구조: Research Paper 페이지 안에 "Papers" 데이터베이스 하나 → 논문 하나 = 행(페이지) 하나.
논문 페이지 안에는 하위 페이지 네 개: 📘 해설 · 📄 원문 · 번역 · 💬 Q&A · (🎤 발표 요약 · 🧩 코드 — 있으면)
- 원문·번역: 절마다 접는 제목(토글 헤딩), 문단마다 원문(회색 인용) → 번역, 수식은 수식 블록
- Q&A: 요약 제목 토글 → 열면 질문 · 답 · 핵심
- 잘라 둔 그림(library/<key>/figs)은 Notion 에 이미지로 올라갑니다.
증분 동기화: 하위 페이지는 한 번 만들면 그대로 두고(링크·댓글 유지), 내용은 조각(해설 섹션 · 번역 절 · Q&A 하나)마다
지문(해시)을 기억해 두었다가 바뀐 조각만 지우고 같은 자리에 다시 넣습니다. 새 Q&A 도 논문 순서상 제자리에 들어갑니다.
데이터베이스·페이지·조각 id 는 data/notion.json 에 둡니다.
"""
import json
import re
import time
import urllib.error
import urllib.request

from . import config, exporter, library
from .utils import LOCK, load_json, now_str, save_json

API = "https://api.notion.com/v1/"
STATE = config.DATA / "notion.json"
MAX_TEXT = 1900   # rich_text 한 조각 최대 2000자


class NotionError(Exception):
    pass


# ---------- HTTP ----------
def _req(method, path, body=None, retry=4):
    if not config.NOTION_TOKEN:
        raise NotionError("Notion 토큰이 없습니다. config.local.json 에 {\"notion_token\": \"ntn_...\"} 을 넣어 주세요.")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": "Bearer " + config.NOTION_TOKEN, "Notion-Version": config.NOTION_VERSION,
        "Content-Type": "application/json"})
    for attempt in range(retry):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                time.sleep(0.34)   # 초당 3회 제한
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < retry - 1:
                time.sleep(float(e.headers.get("Retry-After") or 1.5 * (attempt + 1)))
                continue
            try:
                msg = json.loads(msg).get("message", msg)
            except ValueError:
                pass
            if e.code == 404:
                msg += " — Research Paper 페이지의 ⋯ → 연결(Connections) 에 통합을 추가했는지 확인하세요."
            raise NotionError("Notion {}: {}".format(e.code, msg))
        except urllib.error.URLError as e:
            if attempt < retry - 1:
                time.sleep(2)
                continue
            raise NotionError("Notion 연결 실패: {}".format(e))


# ---------- 글 → rich_text ----------
INLINE_RE = re.compile(r"(\*\*.+?\*\*|\$[^$\n]+?\$|`[^`\n]+?`|\[[^\]\n]+?\]\([^)\s]+\)|(?<![*\w])\*(?![\s*])[^*\n]+?(?<!\s)\*(?![*\w]))")


def rich(text, bold=False, italic=False, color=None):
    """**굵게**, *기울임*, `코드`, $수식$, [링크](url) 를 Notion rich_text 로. 굵게 안의 수식도 살립니다. 긴 글은 나눠 담습니다."""
    out = []
    for part in INLINE_RE.split(text or ""):
        if not part:
            continue
        ann = {"bold": bold, "italic": italic}
        if color:
            ann["color"] = color
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out += rich(part[2:-2], True, italic, color)
            continue
        if part.startswith("$") and part.endswith("$") and len(part) > 2:
            out.append({"type": "equation", "equation": {"expression": part[1:-1][:MAX_TEXT]}, "annotations": ann})
            continue
        if part.startswith("*") and part.endswith("*") and len(part) > 2:
            out += rich(part[1:-1], bold, True, color)
            continue
        link = None
        if part.startswith("`") and part.endswith("`"):
            part, ann = part[1:-1], dict(ann, code=True)
        else:
            m = re.match(r"\[([^\]]+)\]\(([^)\s]+)\)$", part)
            if m:
                part, link = m.group(1), m.group(2)
        for i in range(0, len(part), MAX_TEXT):
            t = {"type": "text", "text": {"content": part[i:i + MAX_TEXT]}, "annotations": ann}
            if link and link.startswith("http"):
                t["text"]["link"] = {"url": link}
            out.append(t)
    return out[:100]


def blk(kind, text="", **kw):
    b = {"object": "block", "type": kind, kind: {"rich_text": rich(text, kw.pop("bold", False), kw.pop("italic", False), kw.pop("color", None))}}
    b[kind].update(kw)
    return b


def equation(expr):
    return {"object": "block", "type": "equation", "equation": {"expression": (expr or "")[:MAX_TEXT]}}


def divider():
    return {"object": "block", "type": "divider", "divider": {}}


def toggle(text, children, heading=None, color=None):
    kind = "heading_{}".format(heading) if heading else "toggle"
    b = blk(kind, text, color=color)
    if heading:
        b[kind]["is_toggleable"] = True
    b[kind]["children"] = children
    return b


# ---------- 마크다운 → 블록 (답변·해설용, 자주 쓰는 문법만) ----------
def md_blocks(text, top=1):
    """top = 가장 큰 제목 단계 (토글 안의 답변은 3 → 모든 제목이 작은 제목)."""
    out = []
    lines = (text or "").replace("\r", "").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("$$"):
            body = s[2:]
            if body.endswith("$$") and len(body) > 2:
                out.append(equation(body[:-2].strip()))
                i += 1
                continue
            buf = [body] if body else []
            i += 1
            while i < len(lines) and "$$" not in lines[i]:
                buf.append(lines[i])
                i += 1
            if i < len(lines):
                tail = lines[i].split("$$")[0]
                if tail.strip():
                    buf.append(tail)
            out.append(equation("\n".join(buf).strip()))
            i += 1
            continue
        if s.startswith("```"):
            lang = s[3:].strip() or "plain text"
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            b = {"object": "block", "type": "code", "code": {"rich_text": [{"type": "text", "text": {"content": "\n".join(buf)[:MAX_TEXT]}}],
                                                                 "language": lang if lang in ("python", "bash", "json", "yaml") else "plain text"}}
            out.append(b)
            i += 1
            continue
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not re.match(r"^\s*\|?\s*:?-{2,}", lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append(table(rows))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            level = min(3, max(top, len(m.group(1))))
            out.append(blk("heading_{}".format(level), m.group(2)))
        elif re.match(r"^[-*]\s+", s):
            out.append(blk("bulleted_list_item", re.sub(r"^[-*]\s+", "", s)))
        elif re.match(r"^\d+[.)]\s+", s):
            out.append(blk("numbered_list_item", re.sub(r"^\d+[.)]\s+", "", s)))
        elif s.startswith(">"):
            out.append(blk("quote", s.lstrip("> ")))
        else:
            # 이어지는 줄은 한 문단으로
            buf = [s]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(r"^(#|[-*]\s|\d+[.)]\s|>|\$\$|```|\|)", lines[i + 1].strip()):
                i += 1
                buf.append(lines[i].strip())
            out.append(blk("paragraph", " ".join(buf)))
        i += 1
    return out


def table(rows):
    width = max(len(r) for r in rows) if rows else 1
    children = []
    for r in rows[:99]:
        cells = [rich(c) for c in r] + [[] for _ in range(width - len(r))]
        children.append({"object": "block", "type": "table_row", "table_row": {"cells": cells}})
    return {"object": "block", "type": "table", "table": {"table_width": width, "has_column_header": True, "has_row_header": False,
                                                           "children": children}}


# ---------- 페이지 내용: (key, 블록 목록) 조각들 ----------
# 조각마다 지문(해시)을 기억해 두고, 다음 동기화 때 바뀐 조각만 지우고 같은 자리에 다시 넣습니다.
# 그림은 {"_image": 경로, "mt": 수정시각} 자리표시로 두었다가 올릴 때 Notion 에 업로드합니다.
def overview_items(key):
    items = [("_head", [blk("callout", "Paper Reader 해설 — 번역을 읽기 전에 먼저 보는 페이지입니다.", icon={"emoji": "📘"},
                            color="gray_background")])]
    group, buf = "_meta", []
    for line in exporter.overview_md(key, heading=False).split("\n"):
        if line.startswith("## "):
            if buf:
                items.append((group, md_blocks("\n".join(buf), top=2)))
            group, buf = line[3:].strip(), [line]
        else:
            buf.append(line)
    if buf:
        items.append((group, md_blocks("\n".join(buf), top=2)))
    return items


def translation_items(key):
    m = library.load_meta(key)
    notes = library.load_notes(key)
    figdir = library.paper_dir(key) / "figs"
    items = [("_head", [blk("callout", "문단마다 원문(회색) → 번역. 절 제목을 누르면 펼쳐집니다.", icon={"emoji": "📄"},
                            color="gray_background")])]
    for i, u in enumerate(m.get("units", [])):
        if u["kind"] == "references":
            continue
        d = library.load_unit(key, i)
        children = []
        for b in (d or {}).get("blocks", []):
            children += block_children(b, notes.get(b["id"]), figdir)
        if not children:
            children = [blk("paragraph", "아직 번역되지 않았습니다.", italic=True, color="gray")]
        items.append((u["id"], [toggle(exporter.unit_heading(u), children, heading=2)]))
    return items


def block_children(b, note=None, figdir=None):
    t = b["type"]
    out = []
    if t == "heading":
        out.append(blk("heading_3", "{}  ·  {}".format(b.get("orig", ""), b.get("ko", ""))))
    elif t == "equation":
        out.append(equation(b.get("latex", "")))
        out.append(blk("paragraph", "{} {}".format(b.get("label", ""), b.get("ko", "")).strip(), color="gray"))
    elif t in ("figure", "table"):
        img = figdir / (b["id"] + ".png") if figdir else None
        if img and img.exists():
            out.append({"_image": str(img), "mt": int(img.stat().st_mtime)})
        out.append(blk("callout", "**{}** {}\n{}".format(b.get("label", ""), b.get("ko", ""), b.get("orig", "")),
                       icon={"emoji": "🖼️" if t == "figure" else "📊"}, color="gray_background"))
    else:
        out.append(blk("quote", b.get("orig", ""), color="gray"))
        out.append(blk("paragraph", b.get("ko", ""), color="yellow_background" if note and note.get("hl") else None))
    if b.get("note"):
        out.append(blk("callout", b["note"], icon={"emoji": "💡"}, color="yellow_background"))
    if note and note.get("memo"):
        out.append(blk("callout", "**내 메모** " + note["memo"], icon={"emoji": "📝"}, color="orange_background"))
    return out


def qa_items(key):
    items = [("_head", [blk("callout", "논문 순서대로 정렬됩니다. 제목을 누르면 펼쳐집니다.", icon={"emoji": "💬"},
                            color="gray_background")])]
    for q in exporter.qa_sorted(key):
        kind = exporter.KIND.get(q.get("kind"), "Q&A")
        head = "[{}] {}".format(q.get("tag", "전체"), q.get("title") or q.get("question", "")[:60])
        meta = " · ".join(x for x in [kind, (q.get("ts") or "")[:10], " ".join("#" + t for t in q.get("tags", []))] if x)
        children = [blk("paragraph", meta, color="gray")]
        if q.get("question"):
            children.append(blk("callout", "**질문** " + q["question"], icon={"emoji": "❓"}, color="gray_background"))
        children += md_blocks(q.get("answer", ""), top=3)
        if q.get("key_point"):
            children.append(blk("callout", "**핵심** " + q["key_point"], icon={"emoji": "📌"}, color="blue_background"))
        items.append((q["id"], [toggle(head, children)]))
    return items


def whole_items(blocks):
    return [("_all", blocks)]


# ---------- 업로드 ----------
def _count(b):
    kids = b[b["type"]].get("children") or [] if "type" in b else []
    return 1 + sum(_count(c) for c in kids)


def upload_image(path):
    """Notion 파일 업로드 API: 업로드 자리 만들기 → 파일 보내기 → id 로 이미지 블록에 붙임."""
    from pathlib import Path
    p = Path(path)
    up = _req("POST", "file_uploads", {"filename": p.name, "content_type": "image/png"})
    boundary = "----rp" + str(int(time.time() * 1000))
    body = ("--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{n}\"\r\nContent-Type: image/png\r\n\r\n".format(
        b=boundary, n=p.name)).encode("utf-8") + p.read_bytes() + "\r\n--{}--\r\n".format(boundary).encode("utf-8")
    req = urllib.request.Request(API + "file_uploads/{}/send".format(up["id"]), data=body, method="POST", headers={
        "Authorization": "Bearer " + config.NOTION_TOKEN, "Notion-Version": config.NOTION_VERSION,
        "Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()
    except urllib.error.HTTPError as e:
        raise NotionError("Notion 그림 업로드 실패 {}: {}".format(e.code, e.read().decode("utf-8", "replace")[:200]))
    time.sleep(0.34)
    return up["id"]


def _resolve(blocks):
    """자리표시 그림을 실제 업로드한 이미지 블록으로 바꿉니다. 업로드가 안 되면 그림만 빼고 계속합니다."""
    out = []
    for b in blocks:
        if "_image" in b:
            try:
                out.append({"object": "block", "type": "image", "image": {"type": "file_upload", "file_upload": {"id": upload_image(b["_image"])}}})
            except NotionError:
                pass
            continue
        kids = b[b["type"]].get("children")
        if kids:
            b[b["type"]]["children"] = _resolve(kids)
        out.append(b)
    return out


def _append(block_id, blocks, after=None):
    """블록을 붙이고 만든 최상위 블록 id 목록을 돌려줍니다. after = 이 블록 바로 뒤에 끼워 넣기.
    Notion 제한: 한 요청에 최상위 100개 · 전체 1000개, 블록 하나의 자식 100개 → 나눠 보냅니다."""
    made_ids = []
    i = 0
    while i < len(blocks):
        chunk, more, total = [], [], 0
        while i < len(blocks) and len(chunk) < 90:
            b = blocks[i]
            kids = b[b["type"]].get("children")
            rest = None
            if kids and len(kids) > 90:
                b[b["type"]]["children"], rest = kids[:90], kids[90:]
            n = _count(b)
            if chunk and total + n > 800:
                if rest:
                    b[b["type"]]["children"] = kids
                break
            chunk.append(b)
            more.append(rest)
            total += n
            i += 1
        body = {"children": chunk}
        if after:
            body["after"] = after
        res = _req("PATCH", "blocks/{}/children".format(block_id), body)
        made = [r["id"] for r in res.get("results", [])][-len(chunk):]
        for rest, mid in zip(more, made):
            if rest:
                _append(mid, rest)
        made_ids += made
        if made:
            after = made[-1]
    return made_ids


def _delete(block_id):
    try:
        _req("DELETE", "blocks/" + block_id)
    except NotionError as e:
        if "404" not in str(e) and "archived" not in str(e).lower():
            raise


def _children(block_id):
    ids, cursor = [], None
    while True:
        res = _req("GET", "blocks/{}/children?page_size=100{}".format(block_id, "&start_cursor=" + cursor if cursor else ""))
        ids += [r["id"] for r in res.get("results", [])]
        if not res.get("has_more"):
            return ids
        cursor = res.get("next_cursor")


def _hash(blocks):
    import hashlib
    return hashlib.sha1(json.dumps(blocks, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def sync_page(page_id, old_items, items):
    """조각 목록을 페이지에 맞춥니다. 바뀌지 않은 조각은 그대로 두고, 바뀐·새 조각만 제자리에 넣습니다.
    old_items = None 이면 (처음이거나 예전 방식으로 만든 페이지) 내용을 비우고 새로 채웁니다.
    돌려주는 값: 새 상태 [{"k", "h", "ids"}], 바뀐 조각 수"""
    new = [(k, _hash(b), b) for k, b in items]
    if old_items is None:
        for bid in _children(page_id):
            _delete(bid)
        old_items = []
    old = {it["k"]: it for it in old_items}
    keep = {k for k, h, _b in new if k in old and old[k]["h"] == h}
    for it in old_items:
        if it["k"] not in keep:
            for bid in it["ids"]:
                _delete(bid)
    state, prev, changed = [], None, 0
    for k, h, b in new:
        if k in keep:
            ids = old[k]["ids"]
        else:
            ids = _append(page_id, _resolve(json.loads(json.dumps(b))), after=prev)
            changed += 1
        state.append({"k": k, "h": h, "ids": ids})
        if ids:
            prev = ids[-1]
    return state, changed


# ---------- 데이터베이스 · 속성 ----------
def _title_prop(text):
    return {"title": [{"type": "text", "text": {"content": text[:200]}}]}


def ensure_database(state):
    if not config.NOTION_PARENT:
        raise NotionError("Notion 부모 페이지가 없습니다. config.local.json 에 \"notion_parent\": \"<페이지 id>\" 를 넣어 주세요.")
    db = state.get("database_id")
    if db:
        try:
            if not _req("GET", "databases/" + db).get("archived"):
                return db
        except NotionError:
            pass
    res = _req("POST", "databases", {
        "parent": {"type": "page_id", "page_id": config.NOTION_PARENT},
        "icon": {"type": "emoji", "emoji": "📚"},
        "title": [{"type": "text", "text": {"content": "Papers"}}],
        "is_inline": True,
        "properties": {
            "제목": {"title": {}},
            "약칭": {"rich_text": {}},
            "저자": {"rich_text": {}},
            "학회": {"select": {}},
            "연도": {"number": {}},
            "Task": {"select": {}},
            "키워드": {"multi_select": {}},
            "읽기": {"select": {"options": [{"name": "읽을 예정", "color": "orange"}, {"name": "읽는 중", "color": "green"},
                                            {"name": "완료", "color": "blue"}]}},
            "번역": {"number": {"format": "percent"}},
            "Q&A": {"number": {}},
            "한 줄 요약": {"rich_text": {}},
            "arXiv": {"url": {}},
            "동기화": {"date": {}},
        },
    })
    state["database_id"] = res["id"]
    return res["id"]


def _props(key):
    m = library.load_meta(key)
    ov = library.load_overview(key) or {}
    done, total = library.progress(m)
    year = re.findall(r"\d{4}", str(m.get("year") or ""))
    p = {
        "제목": _title_prop(m.get("title") or key),
        "약칭": {"rich_text": rich(m.get("short_name") or "")},
        "저자": {"rich_text": rich(", ".join(m.get("authors", []))[:1900])},
        "읽기": {"select": {"name": {"todo": "읽을 예정", "reading": "읽는 중", "done": "완료"}.get(m.get("reading", "todo"), "읽을 예정")}},
        "번역": {"number": round(done / total, 3) if total else 0},
        "Q&A": {"number": len(library.load_qa(key))},
        "한 줄 요약": {"rich_text": rich(ov.get("one_liner", ""))},
        "키워드": {"multi_select": [{"name": k.replace(",", " ")[:90]} for k in (m.get("keywords") or [])[:8]]},
        "동기화": {"date": {"start": time.strftime("%Y-%m-%dT%H:%M:%S+09:00")}},
    }
    if m.get("venue"):
        p["학회"] = {"select": {"name": m["venue"].replace(",", " ")[:90]}}
    if m.get("task"):
        p["Task"] = {"select": {"name": m["task"].replace(",", " ")[:90]}}
    if year:
        p["연도"] = {"number": int(year[0])}
    if m.get("arxiv_id"):
        p["arXiv"] = {"url": "https://arxiv.org/abs/" + m["arxiv_id"]}
    return p


def _alive(page_id):
    try:
        return not _req("GET", "pages/" + page_id).get("archived")
    except NotionError:
        return False


# ---------- 동기화 ----------
def sync(key, progress=lambda *a: None):
    m = library.load_meta(key)
    with LOCK:
        state = load_json(STATE, {}) or {}
    progress(0, 6, "데이터베이스 확인 중")
    db = ensure_database(state)
    info = (state.get("pages") or {}).get(key) or {}

    page_id = info.get("page_id")
    if page_id and _alive(page_id):
        _req("PATCH", "pages/" + page_id, {"properties": _props(key)})
    else:
        res = _req("POST", "pages", {"parent": {"database_id": db}, "icon": {"type": "emoji", "emoji": "📄"}, "properties": _props(key)})
        page_id = res["id"]
        info = {"page_id": page_id, "url": res.get("url"), "subs": {}}
        _append(page_id, [blk("callout", "Paper Reader 가 만든 페이지입니다. 아래 하위 페이지에서 해설 · 원문/번역 · Q&A 를 보세요. "
                                         "PDF: {}".format(m.get("pdf")), icon={"emoji": "🗂️"}, color="gray_background")])

    # 예전 방식(매번 새로 만들던 하위 페이지) 상태를 새 방식으로 옮김: 페이지는 살리고 내용은 한 번 새로 채움
    subs = info.get("subs")
    if subs is None:
        subs = {t: {"page_id": pid, "items": None} for t, pid in (info.get("children") or {}).items()}

    sections = [("📘", "해설", overview_items), ("📄", "원문 · 번역", translation_items), ("💬", "Q&A", qa_items)]
    if (library.paper_dir(key) / "slides.md").exists():
        sections.append(("🎤", "발표 요약", lambda k: whole_items(md_blocks(exporter.slides_md(k)))))
    if (library.paper_dir(key) / "code.json").exists():
        sections.append(("🧩", "코드", lambda k: whole_items(md_blocks(exporter.code_md(k, heading=False)))))
    new_subs, report = {}, []
    for n, (emoji, title, fn) in enumerate(sections, 1):
        progress(n, len(sections) + 1, "맞추는 중: " + title)
        sub = subs.get(title) or {}
        if not sub.get("page_id") or not _alive(sub["page_id"]):
            res = _req("POST", "pages", {"parent": {"page_id": page_id}, "icon": {"type": "emoji", "emoji": emoji},
                                         "properties": {"title": _title_prop(title)}})
            sub = {"page_id": res["id"], "items": []}
        try:
            items, changed = sync_page(sub["page_id"], sub.get("items"), fn(key))
        except NotionError:
            # 사용자가 Notion 에서 블록을 지웠거나 순서가 꼬이면: 이 하위 페이지만 비우고 새로 채움
            items, changed = sync_page(sub["page_id"], None, fn(key))
        new_subs[title] = {"page_id": sub["page_id"], "items": items}
        report.append("{} {}".format(title, changed))
    for title, sub in subs.items():   # 이번에 없는 하위 페이지 정리
        if title not in new_subs and sub.get("page_id"):
            try:
                _req("PATCH", "pages/" + sub["page_id"], {"archived": True})
            except NotionError:
                pass
    info.pop("children", None)
    info.update({"page_id": page_id, "subs": new_subs, "synced": now_str()})
    if not info.get("url"):
        info["url"] = _req("GET", "pages/" + page_id).get("url")
    with LOCK:
        state = load_json(STATE, {}) or {}
        state.setdefault("pages", {})[key] = info
        state["database_id"] = db
        save_json(STATE, state)
    progress(len(sections) + 1, len(sections) + 1, "완료 (바뀐 조각: {})".format(", ".join(report)))
    return info.get("url")
