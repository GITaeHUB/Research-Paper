"""Paper Reader 명령줄.  python rp.py <명령>   (화면은  python rp.py app  또는 논문리더.cmd 더블클릭)

  scan                       papers/ 의 새 PDF 등록
  list                       논문 목록
  analyze <key>              메타데이터·번역 단위·용어집 (Sonnet)
  overview <key>             해설 페이지 (Opus + 웹 확인)
  translate <key> [--unit N ...] [--no-appendix]   번역 (안 된 단위 / 지정 단위 다시)
  ask <key> "질문" [--anchor 3.2-4] [--kind qa|explain|figure|equation]
  slides <key> / code <key>  발표 요약 / 코드 저장소 연결
  export [<key>]             마크다운 다시 만들기 (exports/)
  notion <key>               Notion 동기화
  usage [<key>]              Claude 호출 횟수·추정 비용
  serve / app                서버만 / 서버 + 앱 창
"""
import argparse
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)

from tools import claude, library, pipeline  # noqa: E402


def prog(i, n, msg):
    print("  [{}/{}] {}".format(i, n, msg))


def shortcut():
    """바탕화면에 'Paper Reader' 바로가기 (pythonw 로 rp_app.pyw 실행, 콘솔 창 없음)."""
    import os
    import shutil
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parent
    pyw = shutil.which("pythonw") or sys.executable
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    lnk = desktop / "Paper Reader.lnk"
    ico = root / "assets" / "icon.ico"
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');$s.TargetPath='{exe}';"
          "$s.Arguments='\"{pyw}\"';$s.WorkingDirectory='{root}';$s.IconLocation='{icon},0';$s.Save()").format(
        lnk=str(lnk).replace("'", "''"), exe=pyw.replace("'", "''"), pyw=str(root / "rp_app.pyw").replace("'", "''"),
        root=str(root).replace("'", "''"),
        icon=str(ico).replace("'", "''") if ico.exists() else os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "imageres.dll"))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print("바탕화면에 만들었습니다:", lnk)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("scan")
    sub.add_parser("list")
    for name in ("analyze", "overview", "slides", "code", "notion"):
        sub.add_parser(name).add_argument("key")
    t = sub.add_parser("translate")
    t.add_argument("key")
    t.add_argument("--unit", type=int, nargs="*")
    t.add_argument("--no-appendix", action="store_true")
    a = sub.add_parser("ask")
    a.add_argument("key")
    a.add_argument("question")
    a.add_argument("--anchor", default="")
    a.add_argument("--kind", default="qa", choices=["qa", "explain", "figure", "equation"])
    e = sub.add_parser("export")
    e.add_argument("key", nargs="?")
    u = sub.add_parser("usage")
    u.add_argument("key", nargs="?")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int)
    sub.add_parser("app")
    sub.add_parser("shortcut")
    args = ap.parse_args(argv)

    if args.cmd in (None, "list"):
        library.scan()
        for k in library.all_keys():
            s = library.summary(k)
            print("{:<40} {:>3}/{:<3} Q&A {:<3} {} {} {}".format(k, s["done"], s["total"], s["qa"], s["venue"], s["year"], s["title"]))
        return
    if args.cmd == "scan":
        new = library.scan()
        print("새로 등록:", ", ".join(new) if new else "없음")
        return
    try:
        if args.cmd == "analyze":
            m = pipeline.analyze(args.key, prog)
            print("{} ({} {}) · 번역 단위 {}개".format(m["title"], m.get("venue"), m.get("year"), len(m["units"])))
            for u in m["units"]:
                print("  {:<6} {:<10} p.{}-{}  {}".format(u["id"], u["kind"], u["page_start"], u["page_end"], u["title"]))
        elif args.cmd == "overview":
            d = pipeline.overview(args.key, prog)
            print(d["one_liner"])
        elif args.cmd == "translate":
            pipeline.translate(args.key, prog, only=args.unit, include_appendix=not args.no_appendix)
        elif args.cmd == "ask":
            q = pipeline.ask(args.key, args.question, args.anchor, args.kind)
            print("[{}] {}\n\n{}\n\n핵심: {}".format(q["tag"], q["title"], q["answer"], q["key_point"]))
        elif args.cmd == "slides":
            pipeline.slides(args.key, prog)
        elif args.cmd == "code":
            pipeline.code(args.key, prog)
        elif args.cmd == "export":
            from tools import exporter
            for k in [args.key] if args.key else library.all_keys():
                print(exporter.export(k))
        elif args.cmd == "notion":
            from tools import notion
            print(notion.sync(args.key, prog))
        elif args.cmd == "usage":
            print(claude.usage(args.key))
        elif args.cmd == "serve":
            from tools import server
            server.serve(port=args.port)
        elif args.cmd == "app":
            from tools import app
            app.main()
        elif args.cmd == "shortcut":
            shortcut()
    except claude.ClaudeError as err:
        print("오류:", err, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
