"""Translation setup.

Source strings are English. Mark every user-visible string in Python with ``_()``
and every string in Blueprint with ``_("...")``. Adding a language only needs a
``po/<lang>.po`` file; see ``po/README.md``.
"""

import gettext
import locale
import logging

from kikoe.constants import GETTEXT_DOMAIN
from kikoe.paths import PACKAGE_DIR

log = logging.getLogger(__name__)

LOCALE_DIR = PACKAGE_DIR / "locale"

_translation: gettext.NullTranslations = gettext.NullTranslations()


def setup() -> None:
    """Install translations for Python strings and, where possible, for GtkBuilder."""
    global _translation  # noqa: PLW0603 - module-level singleton set once at startup
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        log.warning("Unsupported locale; falling back to the C locale")

    _translation = gettext.translation(GETTEXT_DOMAIN, LOCALE_DIR, fallback=True)

    # GtkBuilder translates .ui strings through the C library's gettext, which Python
    # only exposes on glibc-like platforms. On Windows/macOS this is missing and .ui
    # strings stay English until we add a fallback (translate in Python after loading).
    if hasattr(locale, "bindtextdomain"):
        locale.bindtextdomain(GETTEXT_DOMAIN, LOCALE_DIR)
        locale.textdomain(GETTEXT_DOMAIN)


def _(message: str) -> str:
    """Translate ``message``."""
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    """Translate a message that depends on a count."""
    return _translation.ngettext(singular, plural, n)
