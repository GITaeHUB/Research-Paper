"""Claude Code 를 명령어 모드(claude -p)로 불러 구조화된 답(JSON)을 받아 옵니다.

- 프로젝트 루트에서 실행하므로 CLAUDE.md 의 번역·답변 규칙이 그대로 적용됩니다.
- 도구는 작업마다 필요한 것만 켭니다 (예: 번역 = Read, 해설 = Read + WebSearch + WebFetch). 파일을 고치거나 명령을 실행하는 도구는 켜지 않습니다.
- --json-schema 로 답의 형식을 정해 두고 structured_output 을 읽습니다.
- Q&A 는 논문마다 세션을 하나 유지합니다 (--session-id 로 시작, --resume 으로 이어감).
- 호출마다 data/claude_log.jsonl 에 모델·시간·비용(정가 기준 추정)을 남깁니다.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import config
from .utils import append_jsonl, now_str

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
LOG_FILE = config.DATA / "claude_log.jsonl"


class ClaudeError(Exception):
    pass


def _version(path):
    m = re.search(r"claude-code-(\d+)\.(\d+)\.(\d+)", str(path))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def find_claude():
    env = os.environ.get("RP_CLAUDE")
    if env and Path(env).exists():
        return env
    w = shutil.which("claude")
    if w:
        return w
    home = Path.home()
    cands = []
    for base in (home / ".vscode" / "extensions", home / ".vscode-insiders" / "extensions", home / ".cursor" / "extensions"):
        if base.exists():
            for name in ("claude.exe", "claude"):
                cands += list(base.glob("anthropic.claude-code-*/resources/native-binary/" + name))
    return str(max(cands, key=_version)) if cands else None


def new_session_id():
    return str(uuid.uuid4())


def _partial_field(buf, field):
    """만들어지는 중인 JSON 글(buf)에서 field 문자열 값을 지금까지 온 만큼 꺼냅니다."""
    m = re.search(r'"{}"\s*:\s*"'.format(field), buf)
    if not m:
        return None
    s, i, out = buf, m.end(), []
    while i < len(s):
        c = s[i]
        if c == '"':
            break
        if c == "\\":
            if i + 1 >= len(s):
                break
            if s[i + 1] == "u":
                if i + 6 > len(s):
                    break
                out.append(s[i:i + 6])
                i += 6
                continue
            out.append(s[i:i + 2])
            i += 2
            continue
        out.append(c)
        i += 1
    try:
        return json.loads('"' + "".join(out) + '"')
    except ValueError:
        return None


def run(prompt, kind, schema=None, tools=(), session_id=None, resume=None, key=None, on_text=None, field="answer"):
    """Claude 를 한 번 부릅니다. 돌려주는 값: {"data": 구조화 출력(dict) 또는 None, "text": 글 답, "session_id", "cost"}

    on_text(글) 를 주면 답이 만들어지는 대로 field(기본 "answer") 의 지금까지 내용을 0.4초 간격으로 넘겨줍니다."""
    exe = find_claude()
    if not exe:
        raise ClaudeError("Claude 실행 파일을 찾지 못했습니다. VS Code 에 Claude Code 확장이 설치되어 있는지 확인하세요. "
                          "(직접 지정하려면 환경변수 RP_CLAUDE 에 claude.exe 경로)")
    model = config.MODELS.get(kind, "sonnet")
    timeout = config.TIMEOUTS.get(kind, 900)
    cmd = [exe, "-p", "--output-format", "stream-json", "--verbose", "--model", model, "--strict-mcp-config"]
    if on_text:
        cmd.append("--include-partial-messages")
    if tools:
        names = ",".join(tools)
        cmd += ["--tools", names, "--allowedTools", names]
    else:
        cmd += ["--tools", ""]
    if schema:
        cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    if resume:
        cmd += ["--resume", resume]
    elif session_id:
        cmd += ["--session-id", session_id]
    else:
        cmd += ["--no-session-persistence"]

    t0 = time.time()
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         cwd=str(config.ROOT), creationflags=CREATE_NO_WINDOW)
    errbuf = []
    threading.Thread(target=lambda: errbuf.append(p.stderr.read()), daemon=True).start()
    timed_out = []

    def kill():
        timed_out.append(True)
        p.kill()
    timer = threading.Timer(timeout, kill)
    timer.start()
    res, tail = None, []
    so_index, buf, last_emit, last_text = None, "", 0.0, None
    try:
        p.stdin.write(prompt.encode("utf-8"))
        p.stdin.close()
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                tail = (tail + [line])[-5:]
                continue
            t = d.get("type")
            if t == "result":
                res = d
            elif t == "stream_event" and on_text:
                ev = d.get("event") or {}
                et = ev.get("type")
                if et == "content_block_start":
                    cb = ev.get("content_block") or {}
                    if cb.get("type") == "tool_use" and cb.get("name") == "StructuredOutput":
                        so_index, buf = ev.get("index"), ""
                elif et == "content_block_delta" and ev.get("index") == so_index:
                    delta = ev.get("delta") or {}
                    if delta.get("type") == "input_json_delta":
                        buf += delta.get("partial_json", "")
                        now = time.time()
                        if now - last_emit > 0.4:
                            text = _partial_field(buf, field)
                            if text and text != last_text:
                                last_emit, last_text = now, text
                                try:
                                    on_text(text)
                                except Exception:  # noqa: BLE001 — 화면 표시용이라 실패해도 무시
                                    pass
        p.wait()
    finally:
        timer.cancel()
    if timed_out:
        _log(kind, model, key, time.time() - t0, None, False, "timeout")
        raise ClaudeError("Claude 가 {}초 안에 끝내지 못했습니다. 다시 시도해 주세요.".format(timeout))
    if not isinstance(res, dict) or res.get("is_error") or p.returncode != 0:
        time.sleep(0.2)
        err = (errbuf[0] if errbuf else b"").decode("utf-8", "replace").strip()
        detail = (res or {}).get("result") if isinstance(res, dict) else None
        msg = " / ".join(x for x in [detail or "", err[-400:], "" if res else " ".join(tail)[-300:]] if x)
        _log(kind, model, key, time.time() - t0, (res or {}).get("total_cost_usd") if isinstance(res, dict) else None, False, msg[:300])
        low = msg.lower()
        if "login" in low or "auth" in low or "credential" in low:
            msg += " — VS Code 에서 Claude 창을 한 번 열어 로그인되어 있는지 확인하세요."
        raise ClaudeError("Claude 호출이 실패했습니다: " + (msg or "알 수 없는 오류"))

    data = res.get("structured_output")
    text = res.get("result") or ""
    if schema and data is None:
        data = _parse_json(text)
        if data is None:
            _log(kind, model, key, time.time() - t0, res.get("total_cost_usd"), False, "no structured output")
            raise ClaudeError("Claude 의 답을 정해진 형식으로 읽지 못했습니다. 다시 시도해 주세요.")
    _log(kind, model, key, time.time() - t0, res.get("total_cost_usd"), True, "")
    return {"data": data, "text": text, "session_id": res.get("session_id"), "cost": res.get("total_cost_usd") or 0}


def _parse_json(text):
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def _log(kind, model, key, sec, cost, ok, err):
    append_jsonl(LOG_FILE, {"ts": now_str(), "kind": kind, "model": model, "key": key, "sec": round(sec, 1),
                            "cost": round(cost or 0, 4), "ok": ok, "err": err})


def usage(key=None):
    """지금까지의 호출 횟수와 추정 비용 (정가 기준. 구독이면 사용량 한도에서 차감됩니다)."""
    from .utils import read_jsonl
    n, cost = 0, 0.0
    for it in read_jsonl(LOG_FILE):
        if key and it.get("key") != key:
            continue
        n += 1
        cost += it.get("cost") or 0
    return {"calls": n, "cost": round(cost, 2)}
