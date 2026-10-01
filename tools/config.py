"""경로 · 모델 · 시간 제한 설정. 개인 설정(Notion 토큰, 모델 바꾸기)은 루트의 config.local.json 에 둡니다 (Git 제외)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAPERS = ROOT / "papers"          # PDF 넣는 곳
LIBRARY = ROOT / "library"        # 논문별 데이터 (원본): library/<key>/*.json
DATA = ROOT / "data"              # 라이브러리 전체 데이터 (색인, 용어집, 비교표, 계보도, 로그)
EXPORTS = ROOT / "exports"        # 사람이 읽는 마크다운 (자동 생성)
WEB = ROOT / "web"                # 프로그램 화면 (html/css/js)

HOST = "127.0.0.1"
PORT = 8765
WINDOW = (1720, 1080)             # 앱 창 크기 (화면보다 크면 Edge 가 알아서 줄임)

# 작업별 모델: 분량이 많은 번역·분석은 Sonnet, 깊이가 필요한 해설·답변은 Opus
MODELS = {
    "analyze": "sonnet",
    "translate": "sonnet",
    "overview": "opus",
    "qa": "opus",
    "slides": "opus",
    "code": "opus",
    "compare": "opus",
    "lineage": "opus",
    "global": "opus",
}

TRANSLATE_PARALLEL = 2            # 번역 단위를 동시에 몇 개씩 (2 → 시간이 거의 절반)

# 작업별 시간 제한 (초)
TIMEOUTS = {
    "analyze": 600,
    "translate": 900,
    "overview": 1500,
    "qa": 900,
    "slides": 900,
    "code": 1200,
    "compare": 1200,
    "lineage": 1500,
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
