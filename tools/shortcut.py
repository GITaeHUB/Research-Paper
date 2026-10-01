"""바탕화면 'Paper Reader' 바로가기 (아이콘 assets/icon.ico, pythonw 로 rp_app.pyw 실행 → 콘솔 창 없음).

.cmd · .pyw 파일은 자기 아이콘을 가질 수 없어서 바로가기로 아이콘을 붙입니다.
처음 프로그램을 열 때 한 번 자동으로 만들고(data/.shortcut 표시), 그 뒤에는 지워도 다시 만들지 않습니다.
다시 만들려면  python rp.py shortcut
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MARK = config.DATA / ".shortcut"


def desktop():
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"


def create():
    root = config.ROOT
    exe = shutil.which("pythonw") or sys.executable.replace("python.exe", "pythonw.exe")
    if not Path(exe).exists():
        exe = sys.executable
    lnk = desktop() / "Paper Reader.lnk"
    ico = root / "assets" / "icon.ico"
    q = lambda p: str(p).replace("'", "''")  # noqa: E731 — PowerShell 작은따옴표 안의 ' 처리
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');$s.TargetPath='{exe}';"
          "$s.Arguments='\"{pyw}\"';$s.WorkingDirectory='{root}';$s.IconLocation='{icon},0';"
          "$s.Description='Paper Reader — 논문 해설·번역·Q&A';$s.Save()").format(
        lnk=q(lnk), exe=q(exe), pyw=q(root / "rp_app.pyw"), root=q(root), icon=q(ico))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, creationflags=CREATE_NO_WINDOW,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    config.DATA.mkdir(exist_ok=True)
    MARK.write_text(str(lnk), encoding="utf-8")
    return lnk


def create_once():
    """처음 실행 때만 만듭니다. 실패해도 프로그램은 그대로 열립니다."""
    if os.name != "nt" or MARK.exists():
        return None
    try:
        if (desktop() / "Paper Reader.lnk").exists():
            config.DATA.mkdir(exist_ok=True)
            MARK.write_text("exists", encoding="utf-8")
            return None
        return create()
    except Exception:  # noqa: BLE001
        return None
