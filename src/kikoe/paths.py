"""Per-user directories, resolved per platform (XDG, ~/Library, %APPDATA%).

Never build user paths by hand; always go through these helpers.
"""

import sys
from pathlib import Path

from platformdirs import PlatformDirs

from kikoe.constants import APP_NAME

PACKAGE_DIR = Path(__file__).resolve().parent

_dirs = PlatformDirs(appname=APP_NAME, appauthor=False, ensure_exists=False)


def bundled_engines_dir() -> Path | None:
    """Local engines shipped inside a frozen build; None when running from source."""
    meipass = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or meipass is None:
        return None
    return Path(meipass) / "local-engines"


def config_dir() -> Path:
    """Settings files (small, user-editable)."""
    return _ensure(_dirs.user_config_path)


def data_dir() -> Path:
    """The note library and retained original recordings."""
    return _ensure(_dirs.user_data_path)


def log_dir() -> Path:
    """Local diagnostic files, never uploaded automatically."""
    return _ensure(_dirs.user_log_path)


def cache_dir() -> Path:
    """Disposable files such as transcoded upload copies."""
    return _ensure(_dirs.user_cache_path)


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
