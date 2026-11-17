"""Entry point for ``python -m src.analyst`` (the analyst lab CLI).

See ``src/analyst/cli.py`` and ``python -m src.analyst --help``. Educational,
synthetic simulator only. All data is synthetic. No PHI.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
