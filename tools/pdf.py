"""PDF 에서 쪽별 글자를 뽑습니다 (Git for Windows 에 들어 있는 pdftotext 사용, 외부 파이썬 패키지 없음).

뽑은 글자는 번역할 때 원문으로 Claude 에게 넘기고(수식·그림은 Claude 가 PDF 쪽을 직접 봄), 검색에도 씁니다.
pdftotext 가 없으면 None 을 돌려주고, 그때는 Claude 가 PDF 를 직접 읽습니다.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def find_pdftotext():
    w = shutil.which("pdftotext")
    if w:
        return w
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 str(Path.home() / "AppData" / "Local" / "Programs")):
        p = Path(base) / "Git" / "mingw64" / "bin" / "pdftotext.exe"
        if p.exists():
            return str(p)
    return None


def page_texts(pdf_path):
    """쪽별 글자 목록 (1쪽 = [0]). 실패하면 None."""
    exe = find_pdftotext()
    if not exe:
        return None
    try:
        p = subprocess.run([exe, "-enc", "UTF-8", str(pdf_path), "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=120, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    pages = p.stdout.decode("utf-8", "replace").split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return [clean(t) for t in pages] or None


def clean(text):
    text = text.replace("\r", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def joined(pages, start=1, end=None):
    """[p.N] 표시를 붙여 여러 쪽을 이어 붙입니다."""
    end = end or len(pages)
    parts = []
    for i in range(max(1, start), min(len(pages), end) + 1):
        parts.append("[p.{}]\n{}".format(i, pages[i - 1]))
    return "\n\n".join(parts)


def _anchor_regex(anchor):
    words = re.findall(r"[A-Za-z0-9]+", anchor or "")[:8]
    if len(words) < 2:
        return None
    return re.compile(r"[^A-Za-z0-9]+".join(re.escape(w) for w in words), re.I)


def slice_unit(pages, start_page, end_page, anchor, next_anchor, next_page):
    """번역 단위의 원문만 잘라 냅니다. 앞뒤 단위의 첫 몇 단어(anchor)로 경계를 찾고, 못 찾으면 쪽 범위 전체를 줍니다.
    돌려주는 값: (원문, 정확히 잘랐는지)"""
    last = max(end_page, next_page or end_page)
    text = joined(pages, start_page, last)
    a = _anchor_regex(anchor)
    s = 0
    exact = True
    if a:
        m = a.search(text)
        if m:
            s = m.start()
        else:
            exact = False
    b = _anchor_regex(next_anchor)
    e = len(text)
    if b:
        m = b.search(text, s + 1)
        if m:
            e = m.start()
        else:
            exact = False
            e = len(joined(pages, start_page, end_page))
    elif next_anchor:
        exact = False
    # 잘라 낸 부분의 시작 쪽 표시를 살려 둡니다
    head = text[:s]
    pm = re.findall(r"\[p\.(\d+)\]", head)
    prefix = "[p.{}]\n".format(pm[-1]) if pm and not text[s:].startswith("[p.") else ""
    return prefix + text[s:e].strip(), exact
