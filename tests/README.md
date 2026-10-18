# Tests

Tests follow the responsibility being exercised, with `test_<module>.py` names:

| Directory | Coverage |
| --- | --- |
| `core/` | Domain rules, formatting, validation and audio calculations |
| `providers/` | Cloud and local adapters, SDK protocols and engine catalogs |
| `media/` | ffmpeg, capture devices and playback |
| `storage/` | SQLite, files, settings and credentials |
| `services/` | Workflows across adapters, cancellation and recovery |
| `ui/` | Widget behavior, controllers and the isolated window scenario |
| `repository/` | Architecture, lazy imports, Blueprint pairing and packaging |
| `shared/` | Internationalization and diagnostics |

Integration tests live with the workflow or adapter they exercise, even when they
use models or storage as setup. Common fixtures live in `conftest.py`; the
display-aware NoteView fixture lives in `ui/conftest.py`. Reusable test utilities
use absolute `tests.*` imports. Test modules do not import other test modules.

Run the full suite with `uv run pytest`, or a group with
`uv run pytest tests/services`. GTK tests need a display; tests that open the main
window use a separate process and temporary data. Headless runs should provide an
isolated Xvfb display to exercise those tests.

Blueprint compilation belongs to the required `uv run scripts/build_ui.py` check,
which also runs in CI before pytest. The pytest suite checks widget/layout pairing
without recompiling every layout.

Keep distinct boundary cases, SDK wire formats, cancellation points, concurrent
writes, transaction rollback, recovery and compatibility checks. Use named
parameter cases when inputs share setup and assertions. Remove a case only when
another test checks the same behavior or the case only exercises standard-library
behavior. In particular, the common HTTP status/message matrix is checked in
`core/test_http_status.py` and `providers/test_provider_errors.py`; SDK adapter
tests retain representative request, authentication, rate-limit and server errors.
