"""Allows `python -m cdimport`."""

import sys

from .cli import main

sys.exit(main())
