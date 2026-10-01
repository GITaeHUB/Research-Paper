"""arXiv 링크·번호로 논문 추가: 제목을 arXiv API 에서 받고, PDF 를 papers/ 에 내려받은 뒤 등록합니다."""
import re
import urllib.request
import xml.etree.ElementTree as ET

from . import config, library
from .utils import safe_name

ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?|([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
UA = {"User-Agent": "PaperReader/1.0 (personal research tool)"}


class ArxivError(Exception):
    pass


def parse_id(text):
    m = ID_RE.search(text or "")
    if not m:
        raise ArxivError("arXiv 번호를 찾지 못했습니다. 예: 2203.17270 또는 https://arxiv.org/abs/2203.17270")
    return m.group(1) or m.group(3)


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_title(aid):
    xml = _get("http://export.arxiv.org/api/query?id_list=" + aid)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = ET.fromstring(xml).find("a:entry", ns)
    t = entry.find("a:title", ns) if entry is not None else None
    if t is None or not (t.text or "").strip() or "Error" in (t.text or ""):
        raise ArxivError("arXiv 에서 {} 를 찾지 못했습니다.".format(aid))
    return re.sub(r"\s+", " ", t.text).strip()


def add(text, progress=lambda *a: None):
    aid = parse_id(text)
    progress(0, 2, "arXiv {} 정보 확인 중".format(aid))
    try:
        title = fetch_title(aid)
    except ArxivError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ArxivError("arXiv 연결 실패: {}".format(e))
    progress(1, 2, "PDF 내려받는 중: " + title)
    data = _get("https://arxiv.org/pdf/" + aid, timeout=180)
    if not data.startswith(b"%PDF"):
        raise ArxivError("PDF 가 아닌 응답을 받았습니다. 잠시 뒤 다시 시도해 주세요.")
    config.PAPERS.mkdir(exist_ok=True)
    path = config.PAPERS / (safe_name(title, 90) + ".pdf")
    path.write_bytes(data)
    new = library.scan()
    key = new[0] if new else None
    if key:
        library.update_meta(key, arxiv_id=aid, title=title)
    progress(2, 2, "추가됨")
    return {"key": key, "title": title, "arxiv_id": aid}
