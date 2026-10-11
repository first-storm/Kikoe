"""Application-wide constants. Change the app ID here only."""

from importlib.metadata import PackageNotFoundError, version

APP_ID = "io.github.first_storm.Kikoe"
APP_NAME = "Kikoe"
GETTEXT_DOMAIN = "kikoe"
WEBSITE = "https://github.com/first-storm/Kikoe"
ISSUE_URL = f"{WEBSITE}/issues"


def _package_version() -> str:
    try:
        return version("kikoe")
    except PackageNotFoundError:  # Running from a source tree that was never installed.
        return "0.0.0"


VERSION = _package_version()
