"""Vercel entry point.

Re-exports the FastAPI ``app`` so the Vercel Python runtime can wrap it as an
ASGI handler. The actual application lives in ``src/gateside/api.py`` — see
that file for the routes.
"""

from __future__ import annotations

import sys
from pathlib import Path

# When deployed to Vercel, the function root is /var/task. Make our `src/`
# package importable from there.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gateside.api import app  # noqa: E402

__all__ = ["app"]
