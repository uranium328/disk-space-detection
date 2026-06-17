"""本機報告伺服器。

提供 --serve 模式：在 127.0.0.1 上服務 HTML 報告，並提供 /open 端點，
讓報告中的「📂 開啟」按鈕能真的用 Windows 檔案總管開啟（或選取）對應路徑。

安全性：
- 只綁定本機 127.0.0.1。
- /open 只接受位於本次掃描根目錄底下、且實際存在的路徑。
- 僅「開啟」檔案總管，不讀取或回傳檔案內容。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse


def _is_under(path: str, roots: list[str]) -> bool:
    """path 是否位於任一掃描根目錄底下（含根目錄本身）。"""
    try:
        rp = os.path.normcase(os.path.abspath(path))
    except OSError:
        return False
    for r in roots:
        rr = os.path.normcase(os.path.abspath(r))
        if rp == rr or rp.startswith(rr.rstrip("\\/") + os.sep):
            return True
    return False


def _reveal(path: str, roots: list[str]) -> tuple[bool, str]:
    """用檔案總管開啟資料夾，或選取檔案。回傳 (成功, 訊息)。"""
    if not path:
        return False, "空路徑"
    if not _is_under(path, roots):
        return False, "路徑不在掃描範圍內"
    if not os.path.exists(path):
        return False, "路徑不存在（可能已被刪除）"
    try:
        norm = os.path.normpath(path)
        if os.path.isdir(norm):
            os.startfile(norm)  # type: ignore[attr-defined]  # 開啟資料夾
        else:
            # 開檔案總管並選取該檔
            subprocess.Popen(["explorer", f"/select,{norm}"])
        return True, "已開啟"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _make_handler(html_path: str, roots: list[str]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # 靜音存取紀錄
            pass

        def _send(self, code: int, body, ctype: str = "application/json"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionAbortedError):
                pass

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html"):
                try:
                    with open(html_path, "rb") as f:
                        self._send(200, f.read(), "text/html")
                except OSError:
                    self._send(500, '{"ok":false,"msg":"無法讀取報告"}')
                return
            if u.path == "/open":
                q = parse_qs(u.query)
                path = unquote((q.get("path") or [""])[0])
                ok, msg = _reveal(path, roots)
                self._send(200 if ok else 400,
                           json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))
                return
            self._send(404, '{"ok":false,"msg":"not found"}')

    return Handler


def serve(html_path: str, roots: list[str], host: str = "127.0.0.1",
          port: int = 8765, open_browser: bool = True) -> None:
    """啟動本機伺服器服務報告，阻塞直到 Ctrl+C。"""
    handler = _make_handler(html_path, roots)
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError:
        # 連接埠被占用 → 改用隨機可用埠
        httpd = ThreadingHTTPServer((host, 0), handler)
    actual_port = httpd.server_address[1]
    url = f"http://{host}:{actual_port}/"
    print(f"報告伺服器已啟動：{url}", file=sys.stderr)
    print("（在此視窗按 Ctrl+C 可結束伺服器）", file=sys.stderr)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n伺服器已停止。", file=sys.stderr)
    finally:
        httpd.server_close()
