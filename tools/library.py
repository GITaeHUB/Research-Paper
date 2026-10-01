"""논문 목록 관리. papers/ 의 PDF 하나 = library/<key>/ 폴더 하나.

library/<key>/
  meta.json        제목·저자·학회·연도·키워드·번역 단위(units)·상태·Q&A 세션
  pages.json       pdftotext 로 뽑은 쪽별 원문 (검색·번역 원문)
  paper.txt        같은 원문을 [p.N] 표시와 함께 이은 글 (Claude 가 Q&A 때 Read 로 봄)
  sections/NN.json 번역된 단위 하나 = 블록 목록 (문단·수식·그림·표)
  overview.json    해설 페이지
  qa.jsonl         Q&A · 깊게 해설 · 그림 해설 · 수식 풀이 (한 줄 = 하나)
  notes.json       블록별 내 메모 · 하이라이트
  glossary.json    논문 용어집
  slides.md / code.json   발표 요약 / 코드 저장소 연결 (버튼으로 만들 때만)

PDF 는 내용(sha1)으로 알아봅니다. 파일 이름을 바꿔도 같은 논문으로 이어집니다.
"""
import hashlib
import re

from . import config, pdf
from .utils import LOCK, load_json, now_str, read_jsonl, save_json, write_text

INDEX_FILE = config.DATA / "index.json"   # {"files": {파일이름: {size, mtime, sha1}}, "keys": {sha1: key}}


def paper_dir(key):
    return config.LIBRARY / key


def meta_path(key):
    return paper_dir(key) / "meta.json"


def load_meta(key):
    return load_json(meta_path(key), None)


def save_meta(meta):
    meta["updated"] = now_str()
    save_json(meta_path(meta["key"]), meta)


def update_meta(key, **fields):
    with LOCK:
        m = load_meta(key)
        m.update(fields)
        save_meta(m)
        return m


def pdf_path(meta):
    return config.PAPERS / meta["pdf"]


def rel_pdf(meta):
    return "papers/" + meta["pdf"]


def _sha1(path):
    h = hashlib.sha1()
    with open(str(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(stem, sha1):
    s = re.sub(r"\([^)]*\)", " ", stem) if len(stem) > 40 else stem
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    s = s[:48].strip("-")
    return s or "paper-" + sha1[:8]


def all_keys():
    if not config.LIBRARY.exists():
        return []
    return sorted(p.name for p in config.LIBRARY.iterdir() if (p / "meta.json").exists())


def scan():
    """papers/ 를 훑어 새 PDF 를 등록하고, 이름이 바뀐 PDF 를 다시 연결합니다. 새로 등록한 key 목록을 돌려줍니다."""
    config.PAPERS.mkdir(exist_ok=True)
    config.LIBRARY.mkdir(exist_ok=True)
    with LOCK:
        idx = load_json(INDEX_FILE, {}) or {}
        files, keys = idx.setdefault("files", {}), idx.setdefault("keys", {})
        seen, new = set(), []
        for f in sorted(config.PAPERS.glob("*.pdf")):
            st = f.stat()
            cached = files.get(f.name)
            if cached and cached["size"] == st.st_size and cached["mtime"] == int(st.st_mtime):
                sha = cached["sha1"]
            else:
                sha = _sha1(f)
                files[f.name] = {"size": st.st_size, "mtime": int(st.st_mtime), "sha1": sha}
            seen.add(sha)
            key = keys.get(sha)
            if key and meta_path(key).exists():
                m = load_meta(key)
                if m.get("pdf") != f.name or m.get("missing"):
                    m["pdf"], m["missing"] = f.name, False
                    save_meta(m)
                continue
            key = slugify(f.stem, sha)
            base, n = key, 2
            while meta_path(key).exists():
                key, n = "{}-{}".format(base, n), n + 1
            keys[sha] = key
            save_meta({"key": key, "pdf": f.name, "sha1": sha, "added": now_str(), "title": f.stem,
                       "status": "new", "units": [], "authors": [], "keywords": []})
            new.append(key)
        for name in [n for n in files if not (config.PAPERS / n).exists()]:
            del files[name]
        for sha, key in keys.items():
            if sha not in seen:
                m = load_meta(key)
                if m and not m.get("missing"):
                    m["missing"] = True
                    save_meta(m)
        save_json(INDEX_FILE, idx)
    return new


def ensure_pages(key):
    """쪽별 원문을 뽑아 두고 돌려줍니다 (한 번만). pdftotext 가 없으면 None."""
    p = paper_dir(key) / "pages.json"
    data = load_json(p, None)
    m = load_meta(key)
    if data and data.get("sha1") == m["sha1"]:
        return data["pages"]
    pages = pdf.page_texts(pdf_path(m))
    if pages is None:
        return None
    save_json(p, {"sha1": m["sha1"], "pages": pages})
    write_text(paper_dir(key) / "paper.txt", pdf.joined(pages))
    if not m.get("num_pages"):
        update_meta(key, num_pages=len(pages))
    return pages


# ---------- 번역 단위 ----------
def unit_file(key, i):
    return paper_dir(key) / "sections" / "{:02d}.json".format(i)


def load_unit(key, i):
    return load_json(unit_file(key, i), None)


def all_blocks(key):
    """번역된 모든 블록을 순서대로: [{unit, unit_title, block...}]"""
    m = load_meta(key) or {}
    out = []
    for i, u in enumerate(m.get("units", [])):
        d = load_unit(key, i)
        if not d:
            continue
        for b in d.get("blocks", []):
            b = dict(b)
            b["unit"], b["unit_title"] = u["id"], u["title"]
            out.append(b)
    return out


def find_block(key, block_id):
    for b in all_blocks(key):
        if b.get("id") == block_id:
            return b
    return None


def progress(meta):
    units = [u for u in meta.get("units", []) if u.get("kind") != "references"]
    done = sum(1 for u in units if u.get("status") == "done")
    return done, len(units)


# ---------- 기타 데이터 ----------
def load_overview(key):
    return load_json(paper_dir(key) / "overview.json", None)


def load_qa(key):
    return read_jsonl(paper_dir(key) / "qa.jsonl")


def load_notes(key):
    return load_json(paper_dir(key) / "notes.json", {}) or {}


def load_glossary(key):
    return load_json(paper_dir(key) / "glossary.json", []) or []


def save_glossary(key, terms):
    seen, out = set(), []
    for t in terms:
        en = (t.get("en") or "").strip()
        if not en or en.lower() in seen:
            continue
        seen.add(en.lower())
        out.append({"en": en, "ko": (t.get("ko") or "").strip(), "note": (t.get("note") or "").strip()})
    out.sort(key=lambda t: t["en"].lower())
    save_json(paper_dir(key) / "glossary.json", out)
    return out


def merge_glossary(key, new_terms):
    """번역하면서 새로 나온 용어를 더합니다. 이미 있는 용어(사용자가 고친 것 포함)는 바꾸지 않습니다."""
    with LOCK:
        cur = load_glossary(key)
        have = {t["en"].lower() for t in cur}
        add = [t for t in new_terms or [] if (t.get("en") or "").strip().lower() not in have]
        if add:
            save_glossary(key, cur + add)


def summary(key):
    """라이브러리 목록 카드에 쓰는 요약."""
    m = load_meta(key)
    done, total = progress(m)
    qa = load_qa(key)
    return {"key": key, "title": m.get("title"), "short": m.get("short_name") or "", "authors": m.get("authors", []),
            "venue": m.get("venue") or "", "year": m.get("year") or "", "keywords": m.get("keywords", []),
            "task": m.get("task") or "", "status": m.get("status"), "reading": m.get("reading", "todo"),
            "done": done, "total": total, "qa": sum(1 for q in qa if q.get("kind") == "qa"), "missing": m.get("missing", False),
            "has_overview": (paper_dir(key) / "overview.json").exists(), "added": m.get("added"), "pdf": m.get("pdf"),
            "folder": load_folders()["assign"].get(key)}


# ---------- 가상 폴더 (실제 파일 위치는 그대로, 프로그램 안에서만 묶어 보기) ----------
FOLDERS_FILE = config.DATA / "folders.json"   # {"folders": [{"id", "name"}], "assign": {key: folder_id}}


def load_folders():
    d = load_json(FOLDERS_FILE, None) or {}
    d.setdefault("folders", [])
    d.setdefault("assign", {})
    return d


def folder_action(body):
    """create {name} · rename {id, name} · delete {id} · move {key, id 또는 null} · order {ids}"""
    import uuid
    act = body.get("action")
    with LOCK:
        d = load_folders()
        if act == "create":
            name = (body.get("name") or "").strip()[:60]
            if not name:
                raise ValueError("폴더 이름을 입력해 주세요.")
            fid = uuid.uuid4().hex[:8]
            d["folders"].append({"id": fid, "name": name})
            if body.get("key"):
                d["assign"][body["key"]] = fid
        elif act == "rename":
            for f in d["folders"]:
                if f["id"] == body.get("id"):
                    f["name"] = (body.get("name") or f["name"]).strip()[:60] or f["name"]
        elif act == "delete":   # 폴더만 지움 — 안의 논문은 미분류로
            d["folders"] = [f for f in d["folders"] if f["id"] != body.get("id")]
            d["assign"] = {k: v for k, v in d["assign"].items() if v != body.get("id")}
        elif act == "move":
            fid = body.get("id")
            if fid and fid in {f["id"] for f in d["folders"]}:
                d["assign"][body["key"]] = fid
            else:
                d["assign"].pop(body.get("key"), None)
        elif act == "order":
            pos = {fid: n for n, fid in enumerate(body.get("ids", []))}
            d["folders"].sort(key=lambda f: pos.get(f["id"], 10 ** 6))
        else:
            raise ValueError("알 수 없는 폴더 작업: {}".format(act))
        save_json(FOLDERS_FILE, d)
    return d


def folder_name(key):
    d = load_folders()
    fid = d["assign"].get(key)
    return next((f["name"] for f in d["folders"] if f["id"] == fid), "")
