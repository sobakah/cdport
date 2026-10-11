#!/usr/bin/env python3
"""Launcher so a repository checkout runs as ./run.py without installing.

Not called cdimport.py: that name would shadow the cdimport/ package.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cdimport.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
