#!/usr/bin/env python3
"""Launcher for cdimport-cover in a checkout: ./cover.py FOLDER...

Does not shadow anything: the package module is cdimport.cover.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdimport.covertool import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
