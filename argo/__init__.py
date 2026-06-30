from __future__ import annotations

import sys
from pathlib import Path

# Ensure api/ is on sys.path so that `app.*` modules are importable
# whenever any argo.* module is imported.
_API_PATH = str(Path(__file__).parent.parent / "api")
if _API_PATH not in sys.path:
    sys.path.insert(0, _API_PATH)
