"""磁碟掃描與大小聚合（核心模組）。

使用 os.scandir 遞迴走訪目錄，自底向上聚合每個目錄的總大小與檔案數，
並於同一趟掃描順便彙整副檔名分布與最大單檔清單。

Windows 注意事項：
- 權限/存取錯誤以 try/except 包住，記錄於 inaccessible 清單，不中斷掃描。
- Reparse point / junction / symlink 一律跳過不遞迴，避免無限迴圈與重複計算。
- 長路徑（>260）加 \\\\?\\ 前綴後再存取。
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

# Windows 檔案屬性：重新剖析點（junction / symlink / mount point）
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# --- 取得「實際磁碟佔用」（size on disk）---------------------------------
# 邏輯大小（st_size）對稀疏檔／壓縮檔會嚴重失真，例如 512GB 的稀疏 .img
# 實際可能只佔幾 GB。GetCompressedFileSizeW 回傳檔案真正用掉的磁碟位元組數。
_INVALID_FILE_SIZE = 0xFFFFFFFF
_get_compressed_size = None
if os.name == "nt":
    try:
        import ctypes
        from ctypes import wintypes

        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _get_compressed_size = _k32.GetCompressedFileSizeW
        _get_compressed_size.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        _get_compressed_size.restype = wintypes.DWORD
    except Exception:
        _get_compressed_size = None


DRIVE_FIXED = 3  # GetDriveTypeW：本機固定磁碟


def list_fixed_drives() -> list:
    """列出本機固定磁碟（DRIVE_FIXED）的根目錄，如 ['C:\\\\', 'D:\\\\']。

    只回傳固定磁碟，刻意排除卸除式（USB）、光碟與網路磁碟機，避免誤掃。
    """
    if os.name != "nt":
        return ["/"]
    import string
    try:
        get_type = ctypes.windll.kernel32.GetDriveTypeW  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        get_type = None
    drives = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if not os.path.exists(root):
            continue
        if get_type is None or get_type(root) == DRIVE_FIXED:
            drives.append(root)
    return drives


def physical_size(path: str, logical: int) -> int:
    """回傳檔案在磁碟上的實際佔用位元組；失敗時退回 logical。"""
    if _get_compressed_size is None:
        return logical
    high = wintypes.DWORD(0)
    low = _get_compressed_size(_long_path(path), ctypes.byref(high))
    if low == _INVALID_FILE_SIZE and ctypes.get_last_error() != 0:
        return logical
    return (high.value << 32) + low


@dataclass
class Node:
    """目錄樹節點。檔案的 children 為 None。"""

    name: str
    path: str
    size: int = 0          # 位元組（目錄為其子樹總和）
    file_count: int = 0    # 子樹內檔案總數（目錄）
    is_dir: bool = True
    children: Optional[list["Node"]] = None

    def to_dict(self, min_size: int, depth: int, max_depth: Optional[int]) -> dict:
        """轉成可序列化字典；低於 min_size 或超過 max_depth 的子節點予以摺疊。"""
        d = {
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "file_count": self.file_count,
            "is_dir": self.is_dir,
        }
        if self.children is not None:
            include_children = max_depth is None or depth < max_depth
            kids = []
            hidden_size = 0
            hidden_count = 0
            for c in sorted(self.children, key=lambda n: n.size, reverse=True):
                if include_children and c.size >= min_size:
                    kids.append(c.to_dict(min_size, depth + 1, max_depth))
                else:
                    hidden_size += c.size
                    hidden_count += 1
            if hidden_count:
                kids.append({
                    "name": f"（其他 {hidden_count} 個較小項目）",
                    "path": "",
                    "size": hidden_size,
                    "file_count": 0,
                    "is_dir": False,
                    "is_aggregate": True,
                })
            d["children"] = kids
        return d


@dataclass
class ScanResult:
    """單一磁碟/資料夾的掃描結果。"""

    root: str
    tree: Node
    ext_sizes: dict = field(default_factory=dict)   # 副檔名 -> [總大小, 數量]
    largest_files: list = field(default_factory=list)  # [(size, path), ...] 已排序取 top
    inaccessible: list = field(default_factory=list)   # 無法存取的路徑
    elapsed: float = 0.0
    size_mode: str = "physical"   # "physical"=實際佔用, "logical"=邏輯大小
    disk_total: int = 0
    disk_used: int = 0
    disk_free: int = 0


def _long_path(path: str) -> str:
    """為超長路徑加上 \\\\?\\ 前綴（僅限絕對路徑）。"""
    if os.name != "nt":
        return path
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):  # UNC 路徑
        return "\\\\?\\UNC\\" + path[2:]
    if len(path) >= 240 and os.path.isabs(path):
        return "\\\\?\\" + path
    return path


def _is_reparse(entry: os.DirEntry) -> bool:
    """判斷 DirEntry 是否為 reparse point（junction/symlink/mount point）。"""
    try:
        if entry.is_symlink():
            return True
    except OSError:
        pass
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
        return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    except (OSError, AttributeError):
        return False


class _Aggregator:
    """掃描期間累積的彙整狀態與進度回饋。"""

    def __init__(self, top_n: int, progress: bool, use_physical: bool):
        self.top_n = top_n
        self.progress = progress
        self.use_physical = use_physical
        self.ext_sizes: dict[str, list[int]] = {}
        self.largest: list[tuple[int, str]] = []  # 維持為已排序（小->大）並截斷
        self.inaccessible: list[str] = []
        self.items_seen = 0
        self.total_size = 0
        self._last_print = 0.0

    def add_file(self, size: int, path: str, ext: str) -> None:
        rec = self.ext_sizes.get(ext)
        if rec is None:
            self.ext_sizes[ext] = [size, 1]
        else:
            rec[0] += size
            rec[1] += 1
        # 維護最大檔 Top-N
        if len(self.largest) < self.top_n:
            self.largest.append((size, path))
            self.largest.sort()
        elif size > self.largest[0][0]:
            self.largest[0] = (size, path)
            self.largest.sort()

        self.items_seen += 1
        self.total_size += size
        if self.progress:
            now = time.time()
            if now - self._last_print > 0.25:
                self._last_print = now
                sys.stderr.write(
                    f"\r掃描中… {self.items_seen:,} 個檔案，"
                    f"{human_size(self.total_size)}    "
                )
                sys.stderr.flush()


def _scan_dir(path: str, name: str, agg: _Aggregator) -> Node:
    """遞迴掃描單一目錄，回傳其 Node。"""
    node = Node(name=name, path=path, is_dir=True, children=[])
    try:
        with os.scandir(_long_path(path)) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if _is_reparse(entry):
                            # junction/symlink：跳過，避免重複計算與迴圈
                            continue
                        child = _scan_dir(entry.path, entry.name, agg)
                        node.size += child.size
                        node.file_count += child.file_count
                        node.children.append(child)
                    else:
                        if _is_reparse(entry):
                            continue
                        size = entry.stat(follow_symlinks=False).st_size
                        if agg.use_physical:
                            size = physical_size(entry.path, size)
                        ext = os.path.splitext(entry.name)[1].lower() or "（無副檔名）"
                        agg.add_file(size, entry.path, ext)
                        node.size += size
                        node.file_count += 1
                        node.children.append(
                            Node(name=entry.name, path=entry.path,
                                 size=size, file_count=1, is_dir=False, children=None)
                        )
                except (PermissionError, OSError):
                    agg.inaccessible.append(entry.path)
    except (PermissionError, OSError):
        agg.inaccessible.append(path)
    return node


def scan(root: str, top_n: int = 50, progress: bool = True,
         use_physical: bool = True) -> ScanResult:
    """掃描一個磁碟或資料夾，回傳 ScanResult。

    root: 例如 'C:\\\\'、'C:'、或任意資料夾路徑。
    top_n: 保留的最大單檔數量。
    progress: 是否於 stderr 顯示掃描進度。
    use_physical: True 用實際磁碟佔用（size on disk），False 用邏輯大小。
    """
    # 正規化磁碟代號："C:" -> "C:\\"
    if len(root) == 2 and root[1] == ":":
        root = root + "\\"
    root = os.path.abspath(root)
    display_name = root

    start = time.time()
    agg = _Aggregator(top_n=top_n, progress=progress, use_physical=use_physical)
    tree = _scan_dir(root, display_name, agg)
    elapsed = time.time() - start

    if progress:
        sys.stderr.write("\r" + " " * 60 + "\r")
        sys.stderr.flush()

    result = ScanResult(
        root=root,
        tree=tree,
        ext_sizes={k: tuple(v) for k, v in agg.ext_sizes.items()},
        largest_files=[(s, p) for s, p in sorted(agg.largest, reverse=True)],
        inaccessible=agg.inaccessible,
        elapsed=elapsed,
        size_mode="physical" if use_physical else "logical",
    )

    # 磁碟整體使用量（若 root 落在某磁碟上）
    try:
        import shutil
        usage = shutil.disk_usage(root)
        result.disk_total = usage.total
        result.disk_used = usage.used
        result.disk_free = usage.free
    except OSError:
        pass

    return result


def human_size(num: int) -> str:
    """位元組轉人類可讀字串。"""
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(value) < 1024.0:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} EB"


def parse_size(text: str) -> int:
    """把 '500MB'、'2GB'、'1024' 之類字串解析為位元組。"""
    text = text.strip().upper()
    multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    for suffix in ("TB", "GB", "MB", "KB", "B"):
        if text.endswith(suffix):
            number = text[: -len(suffix)].strip()
            return int(float(number) * multipliers[suffix])
    return int(float(text))
