"""已知「常見可清理」位置的辨識（僅標記，不刪除）。

此模組只負責把掃描樹中符合已知暫存/快取位置的節點貼上說明標籤，
協助使用者判斷哪些空間通常可以安全回收。**絕不執行刪除動作。**
"""

from __future__ import annotations

import re

# 規則：(正規表示式比對完整路徑（不分大小寫）, 說明)
# 比對對象為節點的絕對路徑，使用正斜線正規化後比對。
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"/windows/temp$", re.I), "Windows 暫存資料夾，通常可清空"),
    (re.compile(r"/users/[^/]+/appdata/local/temp$", re.I), "使用者暫存資料夾，通常可清空"),
    (re.compile(r"/windows/softwaredistribution/download$", re.I), "Windows Update 下載快取，可清理"),
    (re.compile(r"/windows/installer/\$patchcache\$$", re.I), "MSI 修補快取（清理需謹慎）"),
    (re.compile(r"/\$recycle\.bin$", re.I), "資源回收筒，清空即釋放"),
    (re.compile(r"/users/[^/]+/appdata/local/[^/]+/.*cache", re.I), "應用程式快取，通常可清理"),
    (re.compile(r"/appdata/local/google/chrome/user data/.*cache", re.I), "Chrome 快取，可清理"),
    (re.compile(r"/appdata/local/microsoft/edge/user data/.*cache", re.I), "Edge 快取，可清理"),
    (re.compile(r"/appdata/local/mozilla/firefox/profiles/.*cache", re.I), "Firefox 快取，可清理"),
    (re.compile(r"/appdata/local/pip/cache$", re.I), "pip 套件快取，可清理"),
    (re.compile(r"/appdata/local/npm-cache$", re.I), "npm 套件快取，可清理"),
    (re.compile(r"/windows/logs$", re.I), "Windows 記錄檔，可清理"),
    (re.compile(r"/hiberfil\.sys$", re.I), "休眠檔；停用休眠可移除（powercfg /h off），非刪檔"),
    # 注意：pagefile.sys / swapfile.sys 由系統管理、無法手動清除，刻意不標示為可清理。
]


def match(path: str) -> str | None:
    """若 path 命中已知可清理規則，回傳說明字串，否則 None。"""
    norm = path.replace("\\", "/")
    for pattern, desc in _RULES:
        if pattern.search(norm):
            return desc
    return None


def annotate(node: dict) -> None:
    """遞迴在樹字典上加 'cleanup' 欄位（就地修改）。"""
    desc = match(node.get("path", ""))
    if desc:
        node["cleanup"] = desc
    for child in node.get("children", []) or []:
        annotate(child)
