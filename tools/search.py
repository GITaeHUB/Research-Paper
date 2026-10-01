"""라이브러리 전체 검색 (Claude 없이 바로): 원문·번역 문단, Q&A, 내 메모, 해설에서 찾습니다. 띄어 쓴 단어는 모두 들어 있어야 맞음."""
import json
import re

from . import library


def _snip(text, words, width=160):
    low = text.lower()
    pos = min([low.find(w) for w in words if low.find(w) >= 0] or [0])
    s = max(0, pos - width // 3)
    out = text[s:s + width].replace("\n", " ")
    return ("…" if s > 0 else "") + out + ("…" if s + width < len(text) else "")


def search(q, limit=80):
    words = [w.lower() for w in re.split(r"\s+", q.strip()) if w]
    if not words:
        return []

    def hit(text):
        low = (text or "").lower()
        return all(w in low for w in words)

    out = []
    for key in library.all_keys():
        m = library.load_meta(key)
        name = m.get("short_name") or m.get("title")
        notes = library.load_notes(key)
        for b in library.all_blocks(key):
            text = " ".join(b.get(k, "") for k in ("orig", "ko", "latex", "label", "note"))
            if hit(text):
                out.append({"key": key, "paper": name, "where": "본문", "id": b["id"], "tag": "§" + b["id"],
                            "snippet": _snip(b.get("ko") or text, words) if hit(b.get("ko", "")) else _snip(text, words)})
        for qa in library.load_qa(key):
            text = " ".join(qa.get(k, "") for k in ("title", "question", "answer", "key_point"))
            if hit(text):
                out.append({"key": key, "paper": name, "where": "Q&A", "id": qa["id"], "tag": qa.get("tag", ""),
                            "title": qa.get("title"), "snippet": _snip(text, words)})
        for bid, n in notes.items():
            if hit(n.get("memo", "")):
                out.append({"key": key, "paper": name, "where": "메모", "id": bid, "tag": "§" + bid, "snippet": _snip(n["memo"], words)})
        ov = library.load_overview(key)
        if ov:
            text = json.dumps(ov, ensure_ascii=False)
            if hit(text):
                plain = " ".join(str(ov.get(k, "")) for k in ("one_liner", "problem", "key_idea", "task_position", "limitations"))
                out.append({"key": key, "paper": name, "where": "해설", "id": "", "tag": "해설",
                            "snippet": _snip(plain if hit(plain) else text, words)})
        if len(out) >= limit:
            break
    return out[:limit]
