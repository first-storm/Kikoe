"""Entry point for frozen builds; PyInstaller needs a script, not a module."""

import sys

from kikoe.main import main

sys.exit(main())
