"""Allow ``python -m rastreador_convocatorias`` to invoke the CLI.

Handles ``KeyboardInterrupt`` so partial data is not lost when the user
presses Ctrl+C during a long crawl.
"""

from __future__ import annotations

import sys

from rastreador_convocatorias.main import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user — partial data may have been saved", file=sys.stderr)
        sys.exit(130)
