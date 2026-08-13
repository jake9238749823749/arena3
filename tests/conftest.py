from __future__ import annotations

import sys
from pathlib import Path

# Allow `import engine` when pytest is invoked without the pyproject pythonpath.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (str(ROOT), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)
