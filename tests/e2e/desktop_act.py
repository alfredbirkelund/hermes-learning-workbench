"""Small CDP driver for the isolated Desktop (test harness only; the plugin never touches app markup).

Usage: python tests/e2e/desktop_act.py <step> [<step> ...]
Steps:
  click:<visible text>        click the smallest visible element whose text equals/starts with it
  clicksel:<css selector>     click the first element matching the selector
  clickxy:<x>,<y>             click at viewport coordinates (CSS pixels)
  type:<css selector>=<text>  focus an input/textarea and insert text (real key input)
  key:<Key>                   press a key (Enter, Escape, Tab), "key:Ctrl+Enter" for chords
  wait:<seconds>
  waitfor:<js expression>     wait until truthy (30 s)
  shot:<name>                 save .test-artifacts/<name>.png
  text:<css selector>         print innerText of the selector
  js:<expression>             print the JSON value
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import open_page  # noqa: E402

ROOT = Path.home() / ".hermes/cache/scratch/hackathon-learning-workbench"
ART = Path(__file__).resolve().parents[2] / ".test-artifacts"

CLICK_TEXT = r"""
((wanted) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim()
  const all = [...document.querySelectorAll('button, a, [role="button"], [role="tab"], [role="menuitem"], [role="switch"], label, li, summary, div, span')]
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden' }
  let hits = all.filter(el => visible(el) && norm(el.innerText) === wanted)
  if (!hits.length) hits = all.filter(el => visible(el) && norm(el.innerText).startsWith(wanted))
  if (!hits.length) return null
  hits.sort((a, b) => (a.innerText || '').length - (b.innerText || '').length)
  const el = hits[0].closest('button, a, [role="button"], [role="tab"], [role="menuitem"], [role="switch"], summary') || hits[0]
  el.scrollIntoView({ block: 'center' })
  const r = el.getBoundingClientRect()
  return { x: r.x + r.width / 2, y: r.y + r.height / 2, tag: el.tagName, text: norm(el.innerText).slice(0, 60) }
})(%s)
"""

CLICK_SEL = r"""
((sel) => {
  const el = document.querySelector(sel)
  if (!el) return null
  el.scrollIntoView({ block: 'center' })
  const r = el.getBoundingClientRect()
  return { x: r.x + r.width / 2, y: r.y + r.height / 2, tag: el.tagName }
})(%s)
"""


def mouse_click(page, x, y):
    for kind in ("mousePressed", "mouseReleased"):
        page.call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})


def _split_selector(arg):
    """`<css selector>=<text>`: the first `=` outside [attribute] brackets ends the selector."""
    depth = 0
    for i, ch in enumerate(arg):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "=" and depth == 0:
            return arg[:i], arg[i + 1:]
    raise SystemExit(f"type step needs <selector>=<text>: {arg!r}")


def main(steps):
    port = int((ROOT / "desktop-cdp-port").read_text())
    page = open_page(port, timeout=60)
    try:
        for step in steps:
            verb, _, arg = step.partition(":")
            if verb == "click":
                hit = page.wait(CLICK_TEXT % json.dumps(arg), timeout=20)
                mouse_click(page, hit["x"], hit["y"])
                print(f"clicked {hit}")
                time.sleep(0.4)
            elif verb == "clicksel":
                hit = page.wait(CLICK_SEL % json.dumps(arg), timeout=20)
                mouse_click(page, hit["x"], hit["y"])
                print(f"clicked {hit}")
                time.sleep(0.4)
            elif verb == "clickxy":
                x, y = (float(v) for v in arg.split(","))
                mouse_click(page, x, y)
                print(f"clicked at {x},{y}")
                time.sleep(0.4)
            elif verb == "type":
                sel, text = _split_selector(arg)
                hit = page.wait(CLICK_SEL % json.dumps(sel), timeout=20)
                mouse_click(page, hit["x"], hit["y"])
                page.insert_text(text.replace("\\n", "\n"))
                print(f"typed {len(text)} chars into {sel}")
            elif verb == "key":
                if arg.startswith("Ctrl+"):
                    page.key(arg[5:], modifiers=2)
                else:
                    page.key(arg)
                print(f"key {arg}")
            elif verb == "wait":
                time.sleep(float(arg))
            elif verb == "waitfor":
                print("waitfor ->", page.wait(arg, timeout=30))
            elif verb == "shot":
                print("shot", page.screenshot(ART / f"{arg}.png"))
            elif verb == "text":
                print(page.eval(f"(document.querySelector({json.dumps(arg)}) || {{}}).innerText"))
            elif verb == "js":
                print(json.dumps(page.eval(arg), ensure_ascii=False, indent=1)[:5000])
            else:
                raise SystemExit(f"unknown step {step}")
    finally:
        page.close()


if __name__ == "__main__":
    main(sys.argv[1:])
