import sys
from pathlib import Path

from morkovsk.ozon.browser import refresh_cookie

cookie_path = Path(sys.argv[1])
proxy = sys.argv[2] if len(sys.argv) > 2 else None
fresh = refresh_cookie(cookie_path.read_text(encoding="utf-8").strip(), proxy, print)
if not fresh:
    sys.exit(1)
cookie_path.write_text(fresh, encoding="utf-8")
print("новая cookie записана в", cookie_path)
