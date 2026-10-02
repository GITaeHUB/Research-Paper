"""파일 읽기·쓰기 공용 함수. 여러 작업이 동시에 같은 파일을 쓰지 않도록 쓰기는 잠금 하나로 묶습니다."""
import datetime
import json
import os
import re
import threading
import time

LOCK = threading.RLock()


def _replace(tmp, path):
    """os.replace — Windows 에서 다른 쪽이 그 파일을 읽는 중이면 잠깐 막히므로 몇 번 다시 시도합니다."""
    for i in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05)


def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_json(path, default=None):
    """없으면 default. 다른 쪽이 그 파일을 막 바꿔치기하는 중이라 못 읽으면(사용 중 · 반쯤 쓴 내용) 잠깐 기다렸다 다시."""
    for i in range(10):
        try:
            with open(str(path), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except (PermissionError, ValueError):
            if i == 9:
                return default
            time.sleep(0.05)
        except OSError:
            return default
    return default


def save_json(path, data):
    """임시 파일에 쓴 뒤 바꿔치기 → 쓰는 도중 꺼져도 파일이 깨지지 않습니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with LOCK:
        with open(str(tmp), "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        _replace(str(tmp), str(path))


def read_jsonl(path):
    out = []
    try:
        with open(str(path), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def write_jsonl(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with LOCK:
        with open(str(tmp), "w", encoding="utf-8", newline="\n") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        _replace(str(tmp), str(path))


def append_jsonl(path, item):
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        with open(str(path), "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with LOCK:
        with open(str(path), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def safe_name(text, limit=80):
    """Windows 파일 이름에 쓸 수 없는 글자를 지웁니다 (한글은 그대로)."""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", text).strip().strip(".")
    s = re.sub(r"\s+", " ", s)
    return s[:limit].rstrip() or "untitled"
