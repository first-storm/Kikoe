"""Entry point for frozen builds; PyInstaller needs a script, not a module.

``--engine-worker`` runs a local transcription engine instead of the app; the worker
must start without loading GTK, so it is recognised before ``kikoe.main`` is imported.
"""

import sys

if len(sys.argv) > 1 and sys.argv[1] == "--engine-worker":
    from kikoe.providers.local.worker_main import main as worker_main

    sys.exit(worker_main(sys.argv[2:]))

from kikoe.main import main

sys.exit(main())
