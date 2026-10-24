# -*- coding: utf-8 -*-
"""wrapper نازک: بدون آرگومان ← GUI؛ با آرگومان ← CLI (همان آرگومان‌های قبلی -n -m -i -p -o --restart سازگارند).
کد اصلی در بسته‌ی exambank/ است؛ راهنما: python Extraction.WorkFlow.py -h  یا  README.md"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    if len(sys.argv) > 1:
        from exambank.cli import main as cli_main
        sys.exit(cli_main())
    from exambank.gui import main as gui_main
    gui_main()
