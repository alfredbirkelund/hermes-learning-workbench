"""Drive the isolated Desktop over CDP for manual-inspection steps: screenshot + visible text.

Usage: python tests/e2e/desktop_look.py <out.png> [js-expression-to-print]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import open_page  # noqa: E402

root = Path.home() / ".hermes/cache/scratch/hackathon-learning-workbench"
port = int((root / "desktop-cdp-port").read_text())
page = open_page(port, timeout=120)
out = Path(sys.argv[1])
page.screenshot(out)
print("screenshot:", out)
print("url:", page.eval("location.href"))
if len(sys.argv) > 2:
    print(json.dumps(page.eval(sys.argv[2]), ensure_ascii=False, indent=1)[:6000])
else:
    print(page.eval("document.body.innerText.slice(0, 2500)"))
page.close()
