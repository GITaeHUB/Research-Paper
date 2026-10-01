"""프로그램 창: 로컬 서버를 띄우고 Edge 앱 모드(주소창 없는 창)로 엽니다.

- 서버가 이미 떠 있으면(다른 창이 열려 있거나 번역이 백그라운드에서 도는 중) 창만 새로 엽니다.
- 창을 닫아도 번역 같은 작업이 남아 있으면 끝날 때까지 서버가 조용히 일하고, 끝나면 스스로 꺼집니다.
- Edge 가 없으면 Chrome, 그것도 없으면 기본 브라우저로 엽니다.
"""
import os
import socket
import subprocess
import time
import webbrowser
from pathlib import Path

from . import config, jobs, server

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((config.HOST, port)) == 0


def find_browser():
    cands = []
    for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("LOCALAPPDATA", "")):
        if base:
            cands += [Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                      Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"]
    for c in cands:
        if c.exists():
            return str(c)
    return None


def open_window(url):
    exe = find_browser()
    if not exe:
        webbrowser.open(url)
        return None
    w, h = config.WINDOW
    profile = config.ROOT / ".app-profile"   # 이 프로그램 전용 브라우저 프로필 (평소 브라우저와 섞이지 않음)
    args = [exe, "--app=" + url, "--user-data-dir=" + str(profile), "--window-size={},{}".format(w, h),
            "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]
    return subprocess.Popen(args, creationflags=CREATE_NO_WINDOW)


def main():
    from . import shortcut
    shortcut.create_once()   # 처음 열 때 바탕화면 바로가기 (아이콘 포함) 를 한 번 만들어 둠
    url = "http://{}:{}/".format(config.HOST, config.PORT)
    if _port_open(config.PORT):
        open_window(url)
        return
    srv = server.serve_in_thread()
    open_window(url)
    server.LAST_PING[0] = time.time()
    # 창은 5초마다 신호를 보냅니다 (최소화하면 브라우저가 1분에 한 번으로 줄임).
    # Edge 실행 프로세스는 창을 넘겨주고 바로 끝나는 경우가 많아 종료 판단에 쓰지 않고,
    # 신호가 2분 동안 없고 진행 중인 작업도 없을 때만 서버를 끕니다.
    while True:
        time.sleep(5)
        if time.time() - server.LAST_PING[0] > 120 and not jobs.busy():
            break
    srv.shutdown()
