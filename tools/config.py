"""경로 · 모델 · 시간 제한 설정. 개인 설정(Notion 토큰, 모델 바꾸기)은 루트의 config.local.json 에 둡니다 (Git 제외)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"          # PDF 넣는 곳
LIBRARY = ROOT / "library"        # 논문별 데이터 (원본): library/<key>/*.json
DATA = ROOT / "data"              # 라이브러리 전체 데이터 (색인, 용어집, 폴더, 비교표, 로그)
EXPORTS = ROOT / "exports"        # 사람이 읽는 마크다운 (자동 생성)
WEB = ROOT / "web"                # 프로그램 화면 (html/css/js)

HOST = "127.0.0.1"
PORT = 8765
WINDOW = (1720, 1080)             # 앱 창 크기 (화면보다 크면 Edge 가 알아서 줄임)

# 작업별 모델: 분량이 많은 번역·분석은 Sonnet, 깊이가 필요한 해설·답변은 Opus
MODELS = {
    "analyze": "sonnet",
    "meta": "sonnet",
    "translate": "sonnet",
    "overview": "opus",
    "qa": "opus",
    "slides": "opus",
    "code": "opus",
    "compare": "opus",
    "global": "opus",
}

# 생각(thinking)을 얼마나 할지: 낮을수록 출력 토큰 절약. 번역·분석은 낮게, 해설·Q&A 는 기본(None = Claude 기본값)
EFFORT = {"translate": "low", "analyze": "medium", "meta": "low"}

# Q&A 세션 압축: 세션의 입력 토큰이 이만큼 넘으면 다음 질문은 지난 Q&A 요약만 들고 새 세션으로 시작
QA_SESSION_LIMIT = 120000

TRANSLATE_PARALLEL = 2            # 번역 단위를 동시에 몇 개씩 (2 → 시간이 거의 절반)

# 작업별 시간 제한 (초)
TIMEOUTS = {
    "analyze": 600,
    "meta": 300,
    "translate": 900,
    "overview": 1500,
    "qa": 900,
    "slides": 900,
    "code": 1200,
    "compare": 1200,
    "global": 1200,
}

NOTION_TOKEN = ""
NOTION_PARENT = ""                # Papers 데이터베이스를 만들 페이지 id (config.local.json 의 notion_parent)
NOTION_VERSION = "2022-06-28"

LOCAL_FILE = ROOT / "config.local.json"


def _load_local():
    """config.local.json 예: {"notion_token": "ntn_...", "models": {"translate": "opus"}}"""
    global NOTION_TOKEN, NOTION_PARENT
    if not LOCAL_FILE.exists():
        return
    try:
        d = json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
    except ValueError:
        return
    NOTION_TOKEN = d.get("notion_token", NOTION_TOKEN)
    NOTION_PARENT = d.get("notion_parent", NOTION_PARENT)
    MODELS.update(d.get("models", {}))
    TIMEOUTS.update(d.get("timeouts", {}))


_load_local()
