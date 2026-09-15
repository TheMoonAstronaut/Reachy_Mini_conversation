"""reachymini_conversation.__main__ — 让 `python -m reachymini_conversation` 工作。

Python 会自动执行 __main__.py 当用户用 `python -m <package>` 时。
这里只委托到 app.main(),真正的逻辑在 app.py。
"""

from reachymini_conversation.app import main

if __name__ == "__main__":
    import sys

    sys.exit(main())
