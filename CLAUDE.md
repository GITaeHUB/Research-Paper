# CLAUDE.md — Paper Reader (논문 읽기 도구) 규칙

## 이 프로젝트가 무엇인가
- 사용자가 `papers/` 에 넣은 논문 PDF 를 **해설 → 원문·번역 → Q&A** 순서로 읽고, 정리한 것을 로컬 문서(`exports/`)와 Notion 에 남기는 개인 도구입니다.
- 사용자는 **자율주행 관련 딥러닝 / AI / 컴퓨터 비전** 연구자입니다 (BEV perception, 3D detection, motion forecasting, planning 등).
- 답변은 **한국어**, 존댓말. 설계와 진행 계획은 `PLAN.md`, 사용법은 `README.md`.

## 번역 규칙
- 문단마다 **원문 → 번역**. 원문의 문단을 합치거나 나누지 않습니다.
- 번역문은 학술 문체("~한다")로, 자연스러운 한국어로 옮깁니다. 직역투 피하기.
- **전문 용어는 영어 그대로** 두는 것이 기본입니다 (BEV, query, backbone, ego vehicle, occupancy, NLL, minADE 등). 처음 나올 때만 필요하면 괄호로 한국어 뜻.
  일반 단어로 굳은 것은 한국어로 (예: 학습, 추론, 손실 함수, 궤적, 예측 분포).
- 논문별 용어집(`library/<key>/glossary.json`)과 공용 용어집(`data/glossary.json`)의 표기를 반드시 지킵니다.
- 수식은 LaTeX 로 원문 그대로 (`$...$` 인라인, 별행 수식은 equation 블록). 인용 번호 [12] 는 그대로 둡니다.
- 💡 짧은 해설(note)은 정말 어려운 문단에만 1~2문장.

## 답변 규칙 (해설 · Q&A · 깊게 해설 · 그림 해설 · 수식 풀이)
- **연구자 수준으로 정확하게, 그러면서 풀어서.** 풀어 쓰는 방법은 구체적인 단계입니다: 데이터 흐름, 텐서 shape, 수식 한 줄씩 전개, 작은 수치 예.
- **일상 비유는 쓰지 않습니다** ("마치 ~처럼" 식의 엉뚱한 비유 금지). 비교가 필요하면 다른 논문·기법과 비교합니다 (예: "LSS 는 depth 분포로 lift 하고, BEVFormer 는 BEV query 가 이미지 feature 를 sampling").
- 근거 위치를 밝힙니다: §절, Fig., Table, Eq., 쪽. 논문에 없는 내용은 "논문 밖 정보" 라고 구분합니다.
- **관련 논문은 지어내지 않습니다.** 이 논문의 References 에 있거나, 웹에서 실제로 확인한 논문만 이름·연도·학회를 씁니다. 확인 못 한 것은 "미확인" 이라고 표시합니다.
- 결과 수치는 논문 표에서 그대로 옮기고, 추정한 값은 쓰지 않습니다.

## 프로그램에서 온 자동 호출
- 요청이 `[자동 호출]` 로 시작하면 Paper Reader 가 `claude -p` 로 보낸 것입니다 (`tools/claude.py`, 요청문은 `tools/prompts.py`).
- 이때는 **정해진 JSON 형식(--json-schema)으로만** 답합니다. 인사나 앞뒤 설명을 붙이지 않습니다.
- 켜진 도구만 씁니다 (번역 = Read, 해설·Q&A = Read + WebSearch + WebFetch, 라이브러리 질문 = Read + Grep + Glob). 파일을 고치지 않습니다.
- PDF 는 Read 도구의 `pages` 인자로 필요한 쪽만 봅니다 (한 번에 20쪽까지). 원문 글은 `library/<key>/paper.txt` 에 [p.N] 표시와 함께 있습니다.
- Q&A 는 논문마다 세션이 이어집니다(--resume). 앞의 Q&A 맥락을 활용하되, 매번 근거를 다시 확인합니다.

## 대화로 도와줄 때 (VS Code 에서 사용자와 직접 대화)
- 논문 내용 질문을 받으면 `library/<key>/` 의 자료(overview.json, sections/*.json, qa.jsonl, notes.json)를 먼저 확인합니다.
  답한 내용을 Q&A 기록으로 남기고 싶다고 하면 `python rp.py ask <key> "질문" [--anchor 3.2-4]` 를 쓰거나 qa.jsonl 형식으로 추가합니다.
- Notion 동기화는 `python rp.py notion <key>` (토큰·부모 페이지 id 는 `config.local.json`). 토큰이 없으면 claude.ai Notion 커넥터로 직접 올려도 됩니다 (부모 페이지는 `config.local.json` 의 `notion_parent`).
- `config.local.json` 의 토큰을 코드·문서·커밋에 옮겨 적지 않습니다.

## 환경 제약
- Windows 11 + PowerShell + VS Code. **Python 3.8**, 표준 라이브러리만 씁니다 (match 문, `str.removeprefix`, `list[int]` 힌트, `dict | dict` 금지).
- 폴더 이름에 한글·공백이 있습니다. 경로는 따옴표로 감싸고, 코드에서는 `pathlib.Path` 를 씁니다. 파일은 UTF-8 + LF, 열 때 `encoding="utf-8"` 명시.
- PDF 글 추출은 Git for Windows 의 `pdftotext` (`tools/pdf.py`). 화면 라이브러리(marked, KaTeX, mermaid)는 `web/vendor/` 에 받아 둔 것을 씁니다.

## 구조
| 위치 | 내용 |
|---|---|
| `papers/` | PDF (사용자가 넣음) |
| `library/<key>/` | 논문별 데이터 원본: `meta.json`(메타·번역 단위·Q&A 세션), `pages.json`·`paper.txt`(원문), `sections/NN.json`(번역 블록), `overview.json`(해설), `qa.jsonl`, `notes.json`(메모·하이라이트), `glossary.json`, `slides.md`, `code.json` |
| `data/` | `index.json`(PDF↔key), `glossary.json`(공용 용어집), `comparisons.json`, `lineage.json`, `global_qa.jsonl`, `notion.json`, `claude_log.jsonl`(호출·비용 기록) |
| `exports/<논문>/` | **자동 생성** 마크다운: 해설.md · 원문·번역.md · Q&A.md (+ 발표요약.md · 코드.md). 직접 고치지 않습니다 (`python rp.py export`) |
| `tools/` | `config`(모델·시간) · `claude`(호출) · `prompts`(요청문·스키마) · `pipeline`(분석·번역·해설·Q&A·버튼 작업) · `library` · `pdf` · `exporter` · `notion` · `server` · `jobs` · `app` · `search` · `arxiv` |
| `web/` | 화면 (index.html · app.css · app.js) |

- 그림·표 이미지는 화면(PDF.js, `web/app.js` 의 `figureRect`)이 캡션 위치로 잘라 `library/<key>/figs/<블록 id>.png` 에 저장합니다. Notion 동기화가 이것을 이미지로 올립니다.
- 서버 보안 (`tools/server.py`): 켤 때마다 새 토큰 → index.html 에 심음 → /api · /pdf · /fig 는 `X-RP-Token` 헤더나 `?t=` 필요. Host/Origin 은 127.0.0.1/localhost:포트 만. 새 경로를 추가할 때 이 검사를 우회하지 않습니다.
- Notion 은 증분 동기화: `data/notion.json` 에 하위 페이지별 조각(k)·지문(h)·블록 id 를 둡니다.
- 블록 id 는 `<단위 id>-<번호>` (예: `3.2-4`). 화면과 문서에서 `§3.2-4` 로 표시하고, Q&A 의 anchor 로 씁니다.
- 모델은 `tools/config.py` 의 `MODELS` (번역·분석 = sonnet, 나머지 = opus). 개인 설정은 `config.local.json` (Git 제외): `{"notion_token": "...", "models": {...}}`.

## Git 에 올리는 것
- 올림: 코드(`tools/`, `rp.py`, `rp_app.pyw`, `논문리더.cmd`), 화면(`web/`), `assets/`(아이콘), 문서(README·CLAUDE·PLAN), `config.local.example.json`.
- 올리지 않음 (`.gitignore`): `papers/` `library/` `exports/` `data/` `config.local.json` `.app-profile/`. 새 개인 데이터 위치를 만들면 `.gitignore` 에도 넣습니다.

## 하지 말 것
- `exports/` 의 자동 생성 문서를 손으로 고치지 않습니다.
- 관련 논문·수치를 지어내지 않습니다.
- 사용자가 요청하지 않은 Notion 동기화를 하지 않습니다 (버튼·요청 때만).
- 새 기능을 CLI(`rp.py`)에 넣으면 화면(`web/app.js`)과 서버(`tools/server.py`)에도 필요한지 확인합니다.
