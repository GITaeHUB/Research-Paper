"""library/<key>/*.json → exports/<논문 이름>/*.md (사람이 읽는 문서, 자동 생성 — 직접 고치지 않습니다).

해설.md · 원문·번역.md · Q&A.md (+ 발표요약.md · 코드.md 가 있으면). Q&A 는 <details> 토글로 접혀 있습니다.
같은 마크다운 조각을 Notion 동기화에서도 씁니다.
"""
import re
import shutil

from . import config, library
from .utils import LOCK, load_json, safe_name, write_text

PRIORITY = {"must": "꼭 읽기", "later": "나중에", "skip": "건너뛰어도 됨"}
ROLE = {"foundation": "기반", "predecessor": "선행", "concurrent": "동시기", "this": "이 논문", "successor": "후속"}
GROUP = {"foundation": "기반 기법", "predecessor": "직접 선행 연구", "competitor": "경쟁 방법", "successor": "후속 연구", "dataset": "벤치마크·데이터셋"}
KIND = {"qa": "Q&A", "explain": "깊게 해설", "figure": "그림 해설", "equation": "수식 풀이"}


def display_name(m):
    t = m.get("title") or m["key"]
    s = m.get("short_name")
    return "{} — {}".format(s, t) if s and s.lower() not in t.lower()[:len(s) + 2].lower() else t


def unit_heading(u):
    """"3.1" + "3.1 Distance-Based Metrics" → "3.1 Distance-Based Metrics" (번호가 두 번 나오지 않게)"""
    t = re.sub(r"^[A-Z]?[0-9.]*[0-9A-Z]\.?\s+(?=\S)", "", u["title"]) if re.match(r"^[A-Z]?[0-9.]+|^[A-Z]\s", u["title"]) else u["title"]
    if u["id"] in ("abs", "ack", "references") or not re.match(r"^[A-Z]?[0-9.]*[0-9A-Z]?b?c?d?$", u["id"]):
        return t
    return "{} {}".format(u["id"], t)


def export_dir(m):
    return config.EXPORTS / safe_name(m.get("short_name") or m.get("title") or m["key"], 60)


def _link(title, url):
    return "[{}]({})".format(title, url) if url else title


# ---------- 해설 ----------
def overview_md(key, heading=True):
    m = library.load_meta(key)
    ov = library.load_overview(key)
    out = []
    if heading:
        out.append("# 해설 · {}\n".format(display_name(m)))
    meta_line = " · ".join(x for x in [", ".join(m.get("authors", [])[:6]) + (" 외" if len(m.get("authors", [])) > 6 else ""),
                                        "{} {}".format(m.get("venue", ""), m.get("year", "")).strip(), m.get("task", "")] if x)
    if meta_line:
        out.append("> " + meta_line + "\n")
    if not ov:
        out.append("_아직 해설이 없습니다._\n")
        return "\n".join(out)
    out.append("## 한 줄 요약\n" + ov.get("one_liner", "") + "\n")
    out.append("## 읽기 전에 알아야 할 배경")
    for p in ov.get("prerequisites", []):
        out.append("- **{}** — {}".format(p["concept"], p["explain"]))
    out.append("\n## 풀려는 문제\n" + ov.get("problem", "") + "\n")
    out.append("## 핵심 아이디어\n" + ov.get("key_idea", "") + "\n")
    out.append("## 방법 흐름")
    for i, s in enumerate(ov.get("method_flow", []), 1):
        out.append("{}. **{}** — {}{}".format(i, s["step"], s["detail"], "  \n   `{}`".format(s["shape"]) if s.get("shape") else ""))
    out.append("\n## 읽기 가이드")
    for g in ov.get("reading_guide", []):
        out.append("- **[{}]** {} — {}".format(PRIORITY.get(g["priority"], g["priority"]), g["target"], g["why"]))
    out.append("\n## Task 내 위치\n" + ov.get("task_position", "") + "\n")
    tl = ov.get("timeline", [])
    if tl:
        out.append("### 연구 흐름")
        for t in tl:
            name = _link(t["title"], t.get("url"))
            if t.get("role") == "this":
                name = "**{}** ← 이 논문".format(t["title"])
            out.append("- {} · {} {} — {}{}".format(t.get("year", ""), name, t.get("venue", ""), t.get("note", ""),
                                                   "" if t.get("verified", True) else " _(미확인)_"))
        out.append("")
    rel = ov.get("related", [])
    if rel:
        out.append("## 관련 중요 논문")
        for g in GROUP:
            items = [r for r in rel if r.get("group") == g]
            if not items:
                continue
            out.append("### " + GROUP[g])
            for r in items:
                src = "참고문헌 [{}]".format(r["ref_no"]) if r.get("source") == "references" and r.get("ref_no") else \
                    ("웹 확인" if r.get("verified") else "미확인")
                out.append("- {} ({} {}) — {} _({})_".format(_link(r["title"], r.get("url")), r.get("venue", ""), r.get("year", ""),
                                                           r.get("diff", ""), src))
        out.append("")
    res = ov.get("results", [])
    if res:
        out.append("## 결과 요약\n| 벤치마크 | 지표 | 이 논문 | 이전 최고 | 비고 |\n|---|---|---|---|---|")
        for r in res:
            out.append("| {} | {} | {} | {} | {} |".format(*(str(r.get(k, "")).replace("|", "/") for k in
                                                         ("benchmark", "metric", "value", "prev_best", "note"))))
        out.append("")
    if ov.get("results_note"):
        out.append(ov["results_note"] + "\n")
    out.append("## 한계 & 열린 질문\n" + ov.get("limitations", "") + "\n")
    return "\n".join(out)


# ---------- 원문 · 번역 ----------
def apply_marks(text, note, field, left="<mark>", right="</mark>"):
    """드래그로 고른 하이라이트를 글에 표시합니다 (처음 나오는 곳 한 번)."""
    for h in (note or {}).get("hls", []):
        if h.get("f") == field and h.get("s") and h["s"] in text:
            text = text.replace(h["s"], left + h["s"] + right, 1)
    return text


def block_md(b, note=None):
    t = b["type"]
    lines = []
    b = dict(b, orig=apply_marks(b.get("orig", ""), note, "orig"), ko=apply_marks(b.get("ko", ""), note, "ko"))
    if t == "heading":
        lines.append("#### {}  \n_{}_".format(b.get("orig", ""), b.get("ko", "")))
    elif t == "equation":
        lines.append("$$\n{}\n$$".format(b.get("latex", "")) + ("  {}".format(b["label"]) if b.get("label") else ""))
        if b.get("ko"):
            lines.append("→ " + b["ko"])
    elif t in ("figure", "table"):
        lines.append("> **{}** {}\n>\n> **{}** {}".format(b.get("label", ""), b.get("orig", ""), b.get("label", ""), b.get("ko", "")))
    else:
        orig = b.get("orig", "").replace("\n", "\n> ")
        lines.append("<sub>§{}</sub>\n\n> {}\n\n{}".format(b["id"], orig, b.get("ko", "")))
    if b.get("note"):
        lines.append("\n> **해설** " + b["note"])
    if note:
        if note.get("memo"):
            lines.append("\n**내 메모:** " + note["memo"])
    return "\n".join(lines) + "\n"


def translation_md(key, heading=True):
    m = library.load_meta(key)
    notes = library.load_notes(key)
    out = ["# 원문 · 번역 · {}\n".format(display_name(m))] if heading else []
    for i, u in enumerate(m.get("units", [])):
        if u["kind"] == "references":
            continue
        out.append("## {}\n".format(unit_heading(u)))
        d = library.load_unit(key, i)
        if not d:
            out.append("_아직 번역되지 않았습니다._\n")
            continue
        for b in d["blocks"]:
            out.append(block_md(b, notes.get(b["id"])))
    return "\n".join(out)


# ---------- Q&A ----------
def qa_sorted(key, order="section"):
    items = library.load_qa(key)
    if order == "time":
        return items
    m = library.load_meta(key)
    pos = {}
    n = 0
    for i, u in enumerate(m.get("units", [])):
        d = library.load_unit(key, i)
        for b in (d or {}).get("blocks", []):
            pos[b["id"]] = n
            n += 1
    return sorted(items, key=lambda q: (pos.get(q.get("anchor"), -1 if q.get("anchor") == "all" else 10 ** 6), q.get("ts", "")))


def qa_md(key, heading=True):
    m = library.load_meta(key)
    items = qa_sorted(key)
    out = ["# Q&A · {}\n".format(display_name(m))] if heading else []
    out.append("_논문 순서대로 정렬 · {}개_\n".format(len(items)))
    for q in items:
        tags = " ".join("#" + t for t in q.get("tags", []))
        out.append("<details>\n<summary><b>[{}] {}</b> · {} · {} {}</summary>\n".format(
            q.get("tag", "전체"), q.get("title") or q.get("question", "")[:40], KIND.get(q.get("kind"), "Q&A"), q.get("ts", "")[:10], tags))
        if q.get("question"):
            out.append("**질문** {}\n".format(q["question"]))
        out.append(q.get("answer", "") + "\n")
        if q.get("key_point"):
            out.append("> **핵심** {}\n".format(q["key_point"]))
        out.append("</details>\n")
    return "\n".join(out)


# ---------- 코드 ----------
def code_md(key, heading=True):
    m = library.load_meta(key)
    d = load_json(library.paper_dir(key) / "code.json", None)
    if not d:
        return ""
    out = ["# 코드 · {}\n".format(display_name(m))] if heading else []
    tag = "공식" if d.get("official") else "비공식"
    out.append("**저장소:** {} ({}{}) · {}\n".format(d.get("repo_url") or "찾지 못함", tag, "" if d.get("verified") else ", 미확인",
                                                    d.get("framework", "")))
    out.append(d.get("summary", "") + "\n")
    if d.get("mapping"):
        out.append("| 논문 | 코드 | 비고 |\n|---|---|---|")
        for r in d["mapping"]:
            out.append("| {} | {} | {} |".format(r["paper"], _link("`{}`".format(r["path"]), r.get("url")), r.get("note", "")))
        out.append("")
    if d.get("how_to_read"):
        out.append("## 읽는 순서\n" + d["how_to_read"])
    return "\n".join(out)


def slides_md(key):
    p = library.paper_dir(key) / "slides.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ---------- 내보내기 ----------
_MATH_OR_CODE = re.compile(r"(\$\$[\s\S]*?\$\$|\$[^$\n]+\$|`[^`\n]+`)")
_SINGLE_TILDE = re.compile(r"(?<![~\\])~(?!~)")


def guard_tilde(text):
    """물결표 하나(~)를 \\~ 로: 마크다운 뷰어(GFM)는 ~ 두 개 사이를 취소선으로 처리해서
    "p.3~5 … §3.2~3.3" 사이 글이 줄 그어집니다. 코드 블록 · 수식(LaTeX 의 ~ 는 띄어쓰기) 안은 그대로 둡니다."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        parts = _MATH_OR_CODE.split(line)
        out.append("".join(p if i % 2 else _SINGLE_TILDE.sub(r"\\~", p) for i, p in enumerate(parts)))
    return "\n".join(out)


def _write_md(path, text):
    write_text(path, guard_tilde(text))


def export(key):
    m = library.load_meta(key)
    if not m:
        return None
    with LOCK:
        d = export_dir(m)
        old = m.get("export_dir")
        if old and old != d.name and (config.EXPORTS / old).exists() and not d.exists():
            shutil.move(str(config.EXPORTS / old), str(d))
        if old != d.name:
            library.update_meta(key, export_dir=d.name)
    _write_md(d / "해설.md", overview_md(key))
    _write_md(d / "원문·번역.md", translation_md(key))
    _write_md(d / "Q&A.md", qa_md(key))
    s = slides_md(key)
    if s:
        _write_md(d / "발표요약.md", "# 발표 요약 · {}\n\n{}".format(display_name(m), s))
    c = code_md(key)
    if c:
        _write_md(d / "코드.md", c)
    return d
