"""Template translation uses the same Python catalog on every platform."""

import gettext
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from kikoe import i18n


class Catalog(gettext.NullTranslations):
    def gettext(self, message: str) -> str:
        return {"General": "常规", "A & B": "甲与乙"}.get(message, message)

    def pgettext(self, context: str, message: str) -> str:
        return "打开" if context == "verb" and message == "Open" else message


def test_templates_translate_context_and_preserve_nontranslatable_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(i18n, "_translation", Catalog())
    monkeypatch.setattr(i18n, "cache_dir", lambda: tmp_path / "cache")
    source = tmp_path / "widget.ui"
    source.write_text(
        '<interface domain="kikoe"><object>'
        '<property name="title" translatable="yes">General</property>'
        '<property name="label" translatable="yes" context="verb">Open</property>'
        '<property name="tooltip" translatable="yes">A &amp; B</property>'
        '<property name="id">General</property></object></interface>'
    )
    cached = i18n.translated_ui(source)
    assert cached == i18n.translated_ui(source)
    root = ET.parse(cached).getroot()
    assert "domain" not in root.attrib
    assert [p.text for p in root.iter("property")] == ["常规", "打开", "甲与乙", "General"]
    assert all("translatable" not in p.attrib for p in root.iter("property"))
    assert 'translatable="yes"' in source.read_text()
    monkeypatch.setattr(i18n, "_translation", gettext.NullTranslations())
    english = i18n.translated_ui(source)
    assert english != cached
    property_element = ET.parse(english).find("object/property")
    assert property_element is not None and property_element.text == "General"


def test_setup_works_without_native_gettext_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(i18n.locale, "bindtextdomain", raising=False)
    monkeypatch.delattr(i18n.locale, "textdomain", raising=False)
    monkeypatch.setattr(i18n, "LOCALE_DIR", tmp_path)
    monkeypatch.setattr(i18n, "_translation", gettext.NullTranslations())
    i18n.setup()
    assert i18n._("General") == "General"


def test_cache_failure_falls_back_to_original_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "widget.ui"
    source.write_text('<interface><property translatable="yes">General</property></interface>')

    def unavailable() -> Path:
        raise PermissionError("No cache")

    monkeypatch.setattr(i18n, "cache_dir", unavailable)
    assert i18n.translated_ui(source) == source


def test_chinese_catalog_compiles_and_translates_plural_and_template_strings(
    tmp_path: Path,
) -> None:
    compiler = shutil.which("msgfmt")
    if compiler is None:
        pytest.skip("GNU gettext not installed")
    source = Path(__file__).resolve().parent.parent / "po" / "zh_CN.po"
    compiled = tmp_path / "zh.mo"
    subprocess.run([compiler, "--check", "--output-file", str(compiled), str(source)], check=True)
    with compiled.open("rb") as stream:
        catalog = gettext.GNUTranslations(stream)
    assert catalog.gettext("General") == "常规"
    assert catalog.ngettext("{count} recording", "{count} recordings", 5) == "{count} 段录音"
    assert catalog.gettext("_Stop") == "停止(_S)"


def test_system_locale_is_used_without_language_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str] | None] = []

    def translation(
        domain: str, localedir: Path, *, languages: list[str] | None, fallback: bool
    ) -> gettext.NullTranslations:
        calls.append(languages)
        return Catalog()

    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: ("zh_CN", "UTF-8"))
    monkeypatch.setattr(i18n.gettext, "translation", translation)
    monkeypatch.setattr(i18n, "_translation", gettext.NullTranslations())
    i18n.setup()
    assert calls == [["zh_CN"]]
    assert i18n._("General") == "常规"
