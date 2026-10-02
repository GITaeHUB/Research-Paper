"""Claude 에게 보내는 요청문과 답 형식(JSON 스키마). 말투·용어 규칙의 원본은 CLAUDE.md 이고, 여기에는 작업별 지시만 둡니다."""
import json

HEAD = "[자동 호출] Paper Reader 프로그램이 보낸 요청입니다. CLAUDE.md 의 '자동 호출' 규칙을 따르고, 정해진 JSON 형식으로만 답하세요.\n\n"

S = {"type": "string"}
SA = {"type": "array", "items": {"type": "string"}}


def obj(props, required=None):
    return {"type": "object", "properties": props, "required": required if required is not None else list(props)}


def arr(item):
    return {"type": "array", "items": item}


# ---------- 1. 분석 ----------
ANALYZE_SCHEMA = obj({
    "title": S, "short_name": S, "authors": SA, "venue": S, "year": S, "arxiv_id": S, "code_url": S,
    "task": S, "keywords": SA, "abstract_ko": S,
    "units": arr(obj({"id": S, "title": S, "kind": {"type": "string", "enum": ["front", "body", "appendix", "references"]},
                      "page_start": {"type": "integer"}, "page_end": {"type": "integer"}, "anchor": S})),
    "glossary": arr(obj({"en": S, "ko": S, "note": S})),
})


def analyze(meta, text, pdf_rel):
    src = ("아래는 pdftotext 로 뽑은 논문 전체 글입니다 ([p.N] = N쪽 시작).\n<paper>\n" + text + "\n</paper>") if text else \
        ("PDF 파일 `{}` 을 Read 도구로 읽으세요 (한 번에 20쪽까지, pages 인자 사용).".format(pdf_rel))
    return HEAD + """작업: 논문 분석 (메타데이터 + 번역 단위 나누기 + 용어집 초안)

{src}

채울 것
- title(원제), short_name(방법 이름이나 흔히 부르는 약칭, 예: BEVFormer. 없으면 짧게), authors(전원, 순서대로), venue(학회·저널 약칭, 예: CVPR, ECCV, arXiv), year, arxiv_id(없으면 ""), code_url(본문에 있으면, 없으면 "")
- task: 이 논문의 Task 를 영어 표준 명칭으로 (예: Motion Forecasting, 3D Object Detection, BEV Segmentation)
- keywords: 3~6개, 영어
- abstract_ko: 초록을 한국어 2~3문장으로 요약
- units: 번역 단위. 논문 처음부터 끝까지 빠짐없이 순서대로 나눕니다.
  - 첫 단위는 초록: id "abs", kind "front". 제목·저자 줄은 넣지 않습니다.
  - 본문은 절(section)·소절(subsection) 경계로 나누고, 한 단위는 글 분량 약 1.5쪽(700~900단어) 이하가 되게 합니다. 더 길면 문단 경계에서 나누고 id 뒤에 b, c 를 붙입니다 (예: "3.2", "3.2b").
  - id 는 논문의 절 번호 그대로 ("1", "3.2", "A.1"). 번호 없는 절은 짧은 영어 이름 ("ack"). id 에 하이픈(-)은 쓰지 않습니다.
  - kind: 본문 = body, 부록 = appendix, 참고문헌 = references (참고문헌은 한 단위로).
  - page_start/page_end: 그 단위가 걸친 쪽.
  - anchor: 그 단위가 시작하는 곳의 첫 6~10단어를 위 글에 나온 그대로 (절 제목 줄 포함, 예: "3.2 Trajectory Distribution Evaluation Policies"). 나눈 단위(b, c)는 그 문단의 첫 단어들.
- glossary: 이 논문의 핵심 용어 15~30개. en = 영어 원어, ko = 번역문에서 쓸 표기(전문 용어는 영어 그대로 두고 ko 에 같은 영어를 쓰기도 합니다), note = 한 줄 뜻.
""".format(src=src)


# ---------- 2. 번역 ----------
BLOCK = obj({
    "type": {"type": "string", "enum": ["heading", "paragraph", "list", "equation", "figure", "table", "algorithm", "footnote"]},
    "orig": S, "ko": S, "latex": S, "label": S, "note": S, "page": {"type": "integer"},
}, ["type", "orig", "ko", "page"])
TRANSLATE_SCHEMA = obj({"blocks": arr(BLOCK), "terms": arr(obj({"en": S, "ko": S, "note": S}))})


def translate(meta, unit, src, exact, pdf_rel, glossary):
    gl = "\n".join("- {} → {}{}".format(t["en"], t["ko"], " ({})".format(t["note"]) if t.get("note") else "") for t in glossary[:120])
    where = "" if exact else "\n(경계를 정확히 못 잘랐습니다. 아래 글에서 '{}' 단위에 해당하는 부분만 번역하세요.)".format(unit["title"])
    return HEAD + """작업: 번역 단위 하나를 문단마다 원문 → 번역으로 옮기기

논문: {title}
단위: {id} {utitle} (PDF {ps}~{pe}쪽){where}
PDF: `{pdf}` — 아래 글은 pdftotext 로 뽑은 것이라 수식·기호가 깨져 있을 수 있습니다. 수식이 있거나 글이 이상하면 반드시 Read 도구로 PDF 의 해당 쪽(pages 인자)을 보고 바로잡으세요.

<source>
{src}
</source>

용어집 (이 표기를 지키세요)
{gl}

블록 규칙
- 원문 순서대로, 원문의 문단 하나 = paragraph 블록 하나. 문단을 합치거나 나누지 않습니다.
- orig: 원문을 그대로 옮깁니다. 줄바꿈 하이픈만 붙이고, 인라인 수식은 $...$ LaTeX 로, 인용 번호 [12] 는 그대로 둡니다. 쪽 머리말·쪽 번호·줄 번호는 뺍니다.
- ko: 자연스러운 한국어 번역. 용어집 표기를 따르고, 인라인 수식과 인용 번호는 원문과 같게 둡니다.
- heading: 절·소절 제목, 문단 앞 굵은 제목(예: "Implementation details.")은 heading 으로 따로.
- equation: 별행 수식 하나 = 블록 하나. latex = 수식 LaTeX ($$ 없이), label = 식 번호 "(3)" (없으면 ""), orig = "", ko = 이 식이 무엇을 계산하는지 한국어 한 문장.
- figure / table: 캡션만. label = "Fig. 2" / "Table 1", orig = 캡션 원문, ko = 캡션 번역. 표 내용은 옮기지 않습니다.
- list: 글머리표 목록은 한 블록에 줄바꿈으로. algorithm: 알고리즘 상자는 orig 에 원문, ko 에 번역.
- note: 정말 어려운 문단에만 1~2문장 해설 (배경 개념, 앞 내용과의 연결). 비유 금지. 대부분은 "" 입니다.
- page: 그 블록이 있는 쪽.
- terms: 용어집에 없는데 이 단위에서 중요하게 쓰인 용어 (없으면 []).
""".format(title=meta.get("title"), id=unit["id"], utitle=unit["title"], ps=unit["page_start"], pe=unit["page_end"],
           where=where, pdf=pdf_rel, src=src, gl=gl or "(없음)")


# ---------- 3. 해설 페이지 ----------
OVERVIEW_SCHEMA = obj({
    "one_liner": S, "problem": S, "key_idea": S,
    "method_flow": arr(obj({"step": S, "detail": S, "shape": S})),
    "reading_guide": arr(obj({"target": S, "priority": {"type": "string", "enum": ["must", "later", "skip"]}, "why": S})),
    "task_position": S,
    "timeline": arr(obj({"title": S, "year": S, "venue": S,
                         "role": {"type": "string", "enum": ["foundation", "predecessor", "concurrent", "this", "successor"]},
                         "note": S, "url": S, "verified": {"type": "boolean"}})),
    "related": arr(obj({"title": S, "year": S, "venue": S,
                        "group": {"type": "string", "enum": ["foundation", "predecessor", "competitor", "successor", "dataset"]},
                        "diff": S, "url": S, "source": {"type": "string", "enum": ["references", "web"]}, "ref_no": S,
                        "verified": {"type": "boolean"}})),
    "results": arr(obj({"benchmark": S, "metric": S, "value": S, "prev_best": S, "note": S})),
    "results_note": S, "limitations": S,
    "prerequisites": arr(obj({"concept": S, "explain": S})),
})


def overview(meta, text, pdf_rel):
    return HEAD + """작업: 논문 해설 페이지 만들기 — 사용자가 번역을 읽기 **전에** 먼저 읽고 큰 그림을 잡는 페이지입니다.

논문: {title} ({venue} {year}) · Task: {task}
PDF: `{pdf}` (그림·수식·표는 Read 도구로 해당 쪽을 보세요)

<paper>
{text}
</paper>

채울 것 (모두 한국어, 마크다운 허용, 수식은 $...$)
- one_liner: 이 논문이 한 일을 한 문장으로.
- problem: 기존 방법의 어떤 한계·빈틈을 겨냥했는지 (3~6문장).
- key_idea: 방법론의 출발점이 된 관찰/직관과 그것을 어떻게 방법으로 바꿨는지. "무엇을 무엇으로 바꾸면 무엇이 해결된다" 수준으로 분명하게.
- method_flow: 입력 → 각 모듈 → 출력 단계. step = 단계 이름, detail = 하는 일과 이유, shape = 텐서 shape 나 데이터 형태 (예: "N×T×2 궤적 → N×K×T×2"). 4~8단계.
- reading_guide: 꼭 읽을 곳(must), 나중에 봐도 될 곳(later), 건너뛰어도 될 곳(skip). target = "§3.2, Fig. 2, Eq. 4" 처럼 구체적으로.
- task_position: 이 Task 의 연구 흐름 속에서 이 논문의 위치 (어떤 계열을 잇고, 무엇과 다른 길을 갔는지). 4~8문장.
- timeline: 이 Task 의 주요 논문 흐름을 연도순으로 6~12개, 이 논문은 role "this".
- related: 중요한 관련 논문 8~15개. group = foundation(기반 기법) / predecessor(직접 선행) / competitor(같은 시기 경쟁 방법) / successor(후속) / dataset(벤치마크). diff = 이 논문과 무엇이 다른지 한 문장.
- results: 주요 벤치마크 결과 (논문 표에서 그대로). prev_best = 비교 대상 중 가장 좋은 이전 방법과 수치.
- results_note: 결과에서 읽어 낼 점 (ablation 핵심 포함).
- limitations: 저자가 밝힌 한계 + 분석상 약해 보이는 부분 (구분해서).
- prerequisites: 이 논문을 읽기 전에 알아야 할 개념 3~6개와 짧은 설명.

관련 논문 규칙 (매우 중요 — 지어낸 인용 금지)
- 이 논문의 References 에 있는 논문은 source "references", ref_no = 참고문헌 번호, verified true.
- References 에 없는 논문(특히 후속 연구)은 WebSearch/WebFetch 로 실제로 확인한 것만 넣고 source "web", url 을 채우고 verified true.
- 확인하지 못했는데 꼭 언급할 가치가 있으면 verified false 로 두고, 그런 항목은 2개 이하로.
- url 은 확인한 경우에만 (arXiv abs 페이지 권장), 아니면 "".
""".format(title=meta.get("title"), venue=meta.get("venue", ""), year=meta.get("year", ""), task=meta.get("task", ""),
           pdf=pdf_rel, text=text)


# ---------- 4. Q&A ----------
QA_TAGS = ["개념", "방법론", "수식", "실험", "비교", "구현", "그림", "기타"]
QA_SCHEMA = obj({"title": S, "tags": {"type": "array", "items": {"type": "string", "enum": QA_TAGS}},
                 "answer": S, "key_point": S})

KIND_TASK = {
    "qa": None,
    "explain": "이 부분을 깊게 해설해 주세요: 무엇을 말하는지, 왜 이렇게 하는지, 앞뒤 맥락(어디서 나온 이야기이고 뒤에서 어떻게 쓰이는지), 관련 수식이나 텐서 흐름까지.",
    "figure": "이 그림/표를 해설해 주세요: PDF 의 해당 쪽을 Read 로 직접 보고, 무엇을 보여 주는지, 어디부터 보면 되는지, 읽어 내야 할 핵심 관찰, 본문 어느 주장을 뒷받침하는지.",
    "equation": "이 수식을 풀어 주세요: 각 기호의 뜻과 shape → 한 줄씩 전개 → 직관적 의미(비유 말고 수학적·구조적으로) → 이 식이 논문 어디에서 어떻게 쓰이는지.",
}


LENGTH = {
    "short": "답변 길이: **짧게** — 핵심만 3~6문장 (필요하면 식 하나). 목록·제목 없이.",
    "normal": "답변 길이: 보통 — 필요한 만큼, 길어도 화면 한두 쪽.",
    "long": "답변 길이: **자세히** — 단계별로 충분히 (배경 → 전개 → 예 → 논문 안 근거 → 정리).",
}


def history_digest(items, limit=40):
    """새 세션으로 넘길 때 앞 대화를 대신하는 짧은 요약 (지난 Q&A 의 제목 · 핵심)."""
    rows = []
    for q in items[-limit:]:
        rows.append("- [{}] {} — {}".format(q.get("tag", "전체"), q.get("title", ""), q.get("key_point", "")))
    return ("이전 대화 요약 (이 논문에 대해 이미 나눈 Q&A, 필요하면 이어서 참고):" + chr(10) + chr(10).join(rows) + chr(10) * 2) if rows else ""


def session_primer(meta, pdf_rel, paper_txt_rel, units):
    toc = "\n".join("- {} {} (p.{}~{})".format(u["id"], u["title"], u["page_start"], u["page_end"]) for u in units)
    return """이 대화는 논문 한 편에 대한 Q&A 세션입니다. 앞으로 같은 논문에 대한 질문이 이어집니다.

논문: {title} ({venue} {year}) · Task: {task}
- 전체 원문 (쪽 표시 [p.N]): `{txt}`  ← 전체를 한꺼번에 읽지 말고, Grep 으로 필요한 곳을 찾은 뒤 Read 의 offset/limit 으로 그 부분만 읽으세요 (토큰 절약).
- PDF: `{pdf}` (그림·표·수식은 Read 의 pages 인자로 해당 쪽을 직접 보세요)
- 번역: `library/{key}/sections/*.json`, 해설: `library/{key}/overview.json`

목차
{toc}

""".format(title=meta.get("title"), venue=meta.get("venue", ""), year=meta.get("year", ""), task=meta.get("task", ""),
           txt=paper_txt_rel, pdf=pdf_rel, key=meta["key"], toc=toc)


def ask(kind, question, anchor_desc, anchor_text, length="normal"):
    task = KIND_TASK.get(kind)
    parts = [HEAD + "작업: 논문 Q&A", LENGTH.get(length, LENGTH["normal"])]
    if anchor_desc:
        parts.append("질문 위치: " + anchor_desc)
    if anchor_text:
        parts.append("<selected>\n" + anchor_text + "\n</selected>")
    if task:
        parts.append("요청: " + task + ("\n추가로 사용자가 덧붙인 말: " + question if question else ""))
    else:
        parts.append("질문: " + question)
    parts.append("""답 형식
- title: 이 Q&A 가 무엇에 대한 것인지 요약한 한 줄 제목 (질문 문장 그대로 말고, 나중에 목록에서 찾기 쉽게. 25자 안팎)
- tags: 분류 1~2개
- answer: 마크다운 답변. 연구자 수준으로 정확하게, 그러면서 단계(데이터 흐름, 텐서 shape, 수식 전개)로 풀어서. 일상 비유 금지. 근거가 되는 위치(§, Fig., Eq., 쪽)를 밝히고, 논문에 없는 내용은 '논문 밖 정보'라고 구분.
- key_point: 기억할 핵심 한 문장""")
    return "\n\n".join(parts)


# ---------- 5. 버튼으로 만드는 것들 ----------
SLIDES_SCHEMA = obj({"markdown": S})


def slides(meta, overview_json, paper_txt_rel, pdf_rel):
    return HEAD + """작업: 랩미팅 발표용 요약 (슬라이드 5~7장 분량)

논문: {title}
- 원문: `{txt}`, PDF: `{pdf}` (필요하면 Read)
- 이미 만든 해설:
{ov}

markdown 에 슬라이드마다 "## 1. 제목" 으로 시작하고, 개조식 3~5줄 + 필요한 경우 "> 그림: Fig. N" 으로 넣을 그림을 표시하세요.
순서: 문제·동기 → 핵심 아이디어 → 방법 (1~2장) → 실험 결과 → 한계·토론 질문. 한국어, 전문 용어는 영어.
""".format(title=meta.get("title"), txt=paper_txt_rel, pdf=pdf_rel, ov=json.dumps(overview_json or {}, ensure_ascii=False)[:12000])


CODE_SCHEMA = obj({
    "repo_url": S, "official": {"type": "boolean"}, "verified": {"type": "boolean"}, "framework": S, "summary": S,
    "mapping": arr(obj({"paper": S, "path": S, "url": S, "note": S})),
    "how_to_read": S,
})


def code(meta, overview_json, paper_txt_rel, repo_url=None):
    if repo_url:
        how = ("- 사용자가 저장소를 직접 지정했습니다: {}\n"
               "  찾지 말고 이 저장소를 WebFetch 로 열어(README, 파일 목록, 주요 소스) 분석하세요. repo_url 은 이 주소 그대로.\n"
               "  저장소에 들어갈 수 없으면 verified false 로 두고 summary 에 이유.").format(repo_url)
    else:
        how = "- WebSearch / WebFetch 로 공식 저장소(없으면 가장 널리 쓰이는 비공식 구현)를 찾으세요. 찾지 못하면 repo_url \"\" 로 두고 summary 에 이유."
    return HEAD + """작업: 이 논문의 코드 저장소와 논문의 모듈을 코드 파일에 연결하기

논문: {title} ({venue} {year}), 본문에 적힌 코드 주소: {code_url}
원문: `{txt}`
해설의 방법 흐름: {flow}

{how}
- official: 저자 공식 저장소인지. verified: 실제로 페이지를 열어 확인했는지.
- mapping: 논문의 구성 요소(예: "Spatial Cross-Attention (§3.3)", "손실 함수 Eq. 5") → 저장소 안 파일 경로와 GitHub 링크. 실제로 확인한 경로만.
- how_to_read: 코드를 어떤 순서로 보면 되는지 (진입점 → 모델 → 손실 → 설정 파일).
""".format(title=meta.get("title"), venue=meta.get("venue", ""), year=meta.get("year", ""), code_url=meta.get("code_url") or "없음",
           txt=paper_txt_rel, flow=json.dumps((overview_json or {}).get("method_flow", []), ensure_ascii=False)[:4000], how=how)


COMPARE_SCHEMA = obj({"title": S, "columns": SA, "rows": arr(obj({"aspect": S, "values": SA})), "summary": S})


def compare(papers):
    body = "\n\n".join("### [{}] {}\n{}".format(p["key"], p["title"], p["blob"]) for p in papers)
    return HEAD + """작업: 논문 비교표

아래 논문들(각각의 메타데이터와 해설)을 비교하세요. 필요하면 `library/<key>/paper.txt` 를 Read 로 확인하세요.

{body}

- columns: 논문 이름(short_name) 순서대로.
- rows: 비교 항목마다 values 를 columns 순서로. 항목 예: Task, 입력(센서·데이터), 핵심 아이디어, 구조/백본, 학습 목표(손실), 데이터셋·벤치마크, 주요 성능, 장점, 한계, 코드 공개. 서로 비교할 만한 항목 위주로 8~12개.
- 같은 벤치마크 수치는 같은 지표로 맞춰 쓰고, 논문에 없는 값은 "—".
- summary: 마크다운으로 어떤 상황에서 어느 방법이 맞는지, 서로 어떻게 이어지는지.
""".format(body=body)


GLOBAL_SCHEMA = obj({"title": S, "answer": S, "papers": SA})


def global_ask(question, catalog):
    return HEAD + """작업: 라이브러리 전체에 대한 질문

사용자 라이브러리 (key · 제목 · 한 줄 요약):
{catalog}

각 논문 자료: `library/<key>/overview.json` (해설), `library/<key>/paper.txt` (원문), `library/<key>/qa.jsonl` (지난 Q&A), `library/<key>/notes.json` (사용자 메모).
Grep / Read 로 필요한 것을 찾아 답하세요.

질문: {q}

- title: 한 줄 제목, answer: 마크다운 답 (근거 논문을 [key] §위치 로 표시), papers: 답에 쓴 논문 key 목록.
""".format(catalog=catalog, q=question)


# ---------- 저자 · 소속 · 게재처 (첫 1~2쪽만 읽음) ----------
META_SCHEMA = obj({
    "authors_detail": arr(obj({"name": S, "affiliations": {"type": "array", "items": {"type": "integer"}}})),
    "affiliations": SA,
    "venue_full": S,
    "status": {"type": "string", "enum": ["published", "accepted", "preprint", "unknown"]},
})


def meta_info(meta, text, pdf_rel):
    src = ("<first_pages>\n" + text + "\n</first_pages>") if text else "PDF `{}` 의 1~2쪽을 Read 도구(pages 인자)로 읽으세요.".format(pdf_rel)
    return HEAD + """작업: 논문 첫 부분에서 저자 · 소속 · 게재처 뽑기

논문: {title} (지금 알고 있는 학회 표기: {venue} {year}, arXiv: {arxiv})
{src}

- authors_detail: 저자 전원을 순서대로. affiliations 는 아래 affiliations 목록의 번호(1부터)들. 소속을 알 수 없으면 [].
- affiliations: 소속 기관 목록 (저자 각주의 순서대로, 학교·회사 이름만 간결하게, 예: "RWTH Aachen University", "NVIDIA").
- venue_full: 게재처의 정식 이름 + 연도 (예: "IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR) 2025",
  "European Conference on Computer Vision (ECCV) 2026"). 첫 쪽의 학회 표기·저작권 줄·각주("Accepted to ...")에서 찾고,
  학회·저널 표시가 없고 arXiv 에만 있으면 "arXiv preprint (YYYY)".
- status: 학회·저널에 실림 = published, 채택 표기만 있음 = accepted, arXiv 등 미게재 = preprint, 모르겠으면 unknown.
- 적혀 있지 않은 정보는 지어내지 말고 비워 두세요.
""".format(title=meta.get("title"), venue=meta.get("venue") or "-", year=meta.get("year") or "", arxiv=meta.get("arxiv_id") or "-", src=src)
