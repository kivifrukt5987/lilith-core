"""``python -m lilith_core`` — короткая форма запуска сервера."""

from __future__ import annotations

import sys

from .run import main

if __name__ == "__main__":
    sys.exit(main())
