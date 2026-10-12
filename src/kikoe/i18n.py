"""Translation setup.

Source strings are English. Mark every user-visible string in Python with ``_()``
and every string in Blueprint with ``_("...")``. Adding a language only needs a
``po/<lang>.po`` file; see ``po/README.md``.
"""

import gettext
import hashlib
import locale
import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from kikoe.constants import GETTEXT_DOMAIN
from kikoe.paths import PACKAGE_DIR, cache_dir

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

    languages: list[str] | None = None
    if not any(os.environ.get(name) for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")):
        try:
            language = locale.getlocale()[0]
        except ValueError:
            language = None
        if language:
            languages = [language]
    _translation = gettext.translation(
        GETTEXT_DOMAIN, LOCALE_DIR, languages=languages, fallback=True
    )

    # Python also translates template XML, so GTK uses the same catalog on every OS.
    if hasattr(locale, "bindtextdomain") and hasattr(locale, "textdomain"):
        locale.bindtextdomain(GETTEXT_DOMAIN, str(LOCALE_DIR))
        locale.textdomain(GETTEXT_DOMAIN)


def _(message: str) -> str:
    """Translate ``message``."""
    return _translation.gettext(message)


def ngettext(singular: str, plural: str, n: int) -> str:
    """Translate a message that depends on a count."""
    return _translation.ngettext(singular, plural, n)


def translated_ui(path: Path) -> Path:
    """Cache translated templates without depending on a platform's C gettext."""
    source = path.read_bytes()
    root = ET.fromstring(source)
    root.attrib.pop("domain", None)
    for element in root.iter():
        if element.get("translatable", "").lower() not in {"yes", "true", "1"}:
            continue
        message = element.text or ""
        context = element.get("context")
        element.text = _translation.pgettext(context, message) if context else _(message)
        element.attrib.pop("translatable", None)
        element.attrib.pop("context", None)
        element.attrib.pop("comments", None)
    translated = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    digest = hashlib.sha256(translated).hexdigest()
    try:
        directory = cache_dir() / "ui" / digest
        directory.mkdir(parents=True, exist_ok=True)
        cached = directory / path.name
        if not cached.is_file():
            temporary = cached.with_suffix(".tmp")
            temporary.write_bytes(translated)
            temporary.replace(cached)
        return cached
    except OSError:
        log.warning("Translated template cache unavailable; using native GTK translation")
        return path
