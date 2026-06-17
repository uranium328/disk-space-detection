"""命令列介面：解析參數、執行掃描、產生報告。"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser

from .report import build_report
from .scanner import human_size, parse_size, scan


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="disk-space-detection",
        description="掃描磁碟/資料夾的空間佔用，產生互動式 HTML 報告。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="範例：\n"
               "  python main.py C: D: --output report.html\n"
               "  python main.py \"E:\\\\專案\" --min-size 100MB --top 30 --flag-cleanup",
    )
    p.add_argument("drives", nargs="+",
                   help="要掃描的磁碟代號或資料夾路徑，可多個（如 C: D: 或 E:\\folder）")
    p.add_argument("-o", "--output", default="disk_report.html",
                   help="HTML 報告輸出路徑（預設 disk_report.html）")
    p.add_argument("--top", type=int, default=50,
                   help="最大檔案/treemap 顯示數量（預設 50）")
    p.add_argument("--min-size", default="10MB",
                   help="低於此大小的目錄/檔案在樹中摺疊（預設 10MB，可寫 500MB/2GB）")
    p.add_argument("--max-depth", type=int, default=None,
                   help="目錄樹展開深度上限（預設不限）")
    p.add_argument("--flag-cleanup", action="store_true",
                   help="標記常見可清理位置（暫存/快取/休眠檔等；僅標記不刪除）")
    p.add_argument("--no-progress", action="store_true",
                   help="不顯示掃描進度")
    p.add_argument("--no-open", action="store_true",
                   help="完成後不自動開啟瀏覽器")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        min_size = parse_size(args.min_size)
    except ValueError:
        print(f"無法解析 --min-size: {args.min_size!r}", file=sys.stderr)
        return 2

    results = []
    for target in args.drives:
        if not os.path.exists(target if len(target) != 2 or target[1] != ":" else target + "\\"):
            print(f"略過不存在的目標：{target}", file=sys.stderr)
            continue
        print(f"開始掃描 {target} …", file=sys.stderr)
        res = scan(target, top_n=args.top, progress=not args.no_progress)
        print(f"  完成：{human_size(res.tree.size)}、{res.tree.file_count:,} 個檔案、"
              f"耗時 {res.elapsed:.1f}s"
              + (f"、{len(res.inaccessible)} 個項目無法存取" if res.inaccessible else ""),
              file=sys.stderr)
        results.append(res)

    if not results:
        print("沒有可掃描的有效目標。", file=sys.stderr)
        return 1

    out = build_report(
        results,
        output_path=args.output,
        min_size=min_size,
        max_depth=args.max_depth,
        top_n=args.top,
        flag_cleanup=args.flag_cleanup,
    )
    abs_out = os.path.abspath(out)
    print(f"\n報告已產生：{abs_out}", file=sys.stderr)

    if not args.no_open:
        try:
            webbrowser.open("file:///" + abs_out.replace("\\", "/"))
        except Exception:
            pass
    return 0
