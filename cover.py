#!/usr/bin/env python3
"""Launcher for cdport-cover in a checkout: ./cover.py FOLDER...

Does not shadow anything: the package module is cdport.cover.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdport.covertool import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
