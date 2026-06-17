"""進入點。

用法：
    python main.py C: D: --output report.html
    python main.py "E:\\some\\folder" --min-size 100MB --flag-cleanup
"""

import sys

from disk_scan.cli import main

if __name__ == "__main__":
    sys.exit(main())
