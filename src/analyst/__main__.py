"""Entry point for ``python -m src.analyst`` (the analyst lab CLI).

See ``src/analyst/cli.py`` and ``python -m src.analyst --help``. Educational,
synthetic simulator only. All data is synthetic. No PHI.
"""

from __future__ import annotations

import sys

from .cli import main
from .common import EXIT_INTERNAL

if __name__ == "__main__":
    exit_code = main()
    if exit_code == EXIT_INTERNAL:
        try:
            sys.stdout.flush()
        except Exception:
            # A failed buffered write can be retried at interpreter shutdown,
            # replacing the documented exit 6 with 120. Only the process
            # entrypoint owns this cleanup; embedded main() keeps its stream.
            sys.stdout = None
    raise SystemExit(exit_code)
