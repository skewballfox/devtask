#!/usr/bin/env python3
"""Claude Code hook entry point; the logic is in `devtask.guard`.

The plugin runs this from its own copy of the repository with the system
python3, so it imports the package from `src/` instead of relying on an
installed devtask.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from devtask.guard import main

main()
