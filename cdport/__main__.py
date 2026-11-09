"""Allows `python -m cdport`."""

import sys

from .cli import main

sys.exit(main())
