#!/usr/bin/env python3
"""
EML-to-Files launcher.

  Double-click (no arguments)  →  opens the GUI
  Command-line arguments       →  runs the CLI  (python main.py --help)
"""

import multiprocessing
import sys


def main() -> None:
    multiprocessing.freeze_support()

    if len(sys.argv) > 1:
        from convert import main as cli_main
        cli_main()
    else:
        from gui import App
        App().mainloop()


if __name__ == "__main__":
    main()
