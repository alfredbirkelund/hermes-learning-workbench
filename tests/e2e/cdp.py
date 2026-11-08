"""Tiny Chrome DevTools Protocol client for driving the isolated Hermes Desktop in tests.

Uses only the stdlib plus ``websockets`` (shipped in Hermes's environment). Test-harness code: it is
allowed to query DOM nodes of the plugin under test; the plugin itself never touches app markup.
"""
from __future__ import annotations

import base64
import itertools
import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

from websockets.sync.client import connect


def targets(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as resp:
        return json.loads(resp.read().decode())


def wait_for_page(port: int, timeout: float = 90.0, url_hint: str = "") -> dict:
    deadline = time.time() + timeout
    last: Any = None
    while time.time() < deadline:
        try:
            pages = [t for t in targets(port) if t.get("type") == "page" and url_hint in t.get("url", "")]
            # prefer the main renderer (index.html), not devtools or blank pages
            pages.sort(key=lambda t: ("index.html" not in t.get("url", ""), t.get("url", "")))
            if pages:
                return pages[0]
        except Exception as exc:  # port not up yet
            last = exc
        time.sleep(0.5)
    raise TimeoutError(f"no Desktop page on CDP port {port}: {last}")


class Page:
    def __init__(self, ws_url: str):
        self.ws = connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=20)
        self.ids = itertools.count(1)

    def close(self) -> None:
        self.ws.close()

    def call(self, method: str, params: Optional[dict] = None, timeout: float = 30.0) -> dict:
        mid = next(self.ids)
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv(timeout=max(0.1, deadline - time.time())))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

    def eval(self, expression: str, *, await_promise: bool = True, timeout: float = 30.0) -> Any:
        res = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": await_promise,
                                             "returnByValue": True, "userGesture": True}, timeout=timeout)
        if res.get("exceptionDetails"):
            raise RuntimeError(json.dumps(res["exceptionDetails"])[:2000])
        return res.get("result", {}).get("value")

    def wait(self, expression: str, timeout: float = 30.0, interval: float = 0.25) -> Any:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                last = self.eval(expression)
                if last:
                    return last
            except Exception as exc:
                last = exc
            time.sleep(interval)
        raise TimeoutError(f"condition not met: {expression[:200]} (last={last!r})")

    def insert_text(self, text: str) -> None:
        self.call("Input.insertText", {"text": text})

    def key(self, key: str, code: Optional[str] = None, modifiers: int = 0, text: Optional[str] = None) -> None:
        base = {"key": key, "code": code or key, "modifiers": modifiers}
        if key == "Enter":
            base.update(windowsVirtualKeyCode=13, nativeVirtualKeyCode=13, text="\r")
        elif key == "Tab":
            base.update(windowsVirtualKeyCode=9, nativeVirtualKeyCode=9)
        elif key == "Escape":
            base.update(windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
        elif text:
            base["text"] = text
        self.call("Input.dispatchKeyEvent", {"type": "keyDown", **base})
        self.call("Input.dispatchKeyEvent", {"type": "keyUp", **{k: v for k, v in base.items() if k != "text"}})

    def screenshot(self, path: Path) -> Path:
        data = self.call("Page.captureScreenshot", {"format": "png"})["data"]
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
        return path

    def set_viewport(self, width: int, height: int) -> None:
        self.call("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})

    def clear_viewport(self) -> None:
        self.call("Emulation.clearDeviceMetricsOverride")


def open_page(port: int, timeout: float = 90.0) -> Page:
    page = wait_for_page(port, timeout=timeout)
    return Page(page["webSocketDebuggerUrl"])
