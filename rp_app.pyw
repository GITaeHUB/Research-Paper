"""더블클릭으로 여는 프로그램 창 (.pyw 는 콘솔 창 없이 실행됩니다).

열리지 않으면 같은 폴더의 '논문리더.cmd' 를 더블클릭하거나, 터미널에서  python rp.py app  을 실행하세요.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tools.app import main  # noqa: E402

if __name__ == "__main__":
    main()
