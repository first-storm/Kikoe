"""Template translation uses the same Python catalog on every platform."""

import gettext
import locale
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

import pytest

from kikoe import i18n


@pytest.fixture(autouse=True)
def _restore_locale() -> Iterator[None]:
    """``i18n.setup()`` changes the process locale, which would leak into later tests."""
    saved = locale.setlocale(locale.LC_ALL)
    yield
    locale.setlocale(locale.LC_ALL, saved)


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
    source = Path(__file__).resolve().parents[2] / "po" / "zh_Hans.po"
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
    assert calls == [["zh_Hans"]]
    assert i18n._("General") == "常规"


PO_DIR = Path(__file__).resolve().parents[2] / "po"
LANGUAGES = (PO_DIR / "LINGUAS").read_text().split()


@pytest.fixture(scope="module")
def compiled_catalogs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Exercise shipped catalogs without depending on stale developer build artifacts."""
    compiler = shutil.which("msgfmt")
    if compiler is None:
        pytest.skip("GNU gettext not installed")
    directory = tmp_path_factory.mktemp("catalogs")
    for language in LANGUAGES:
        output = directory / language / "LC_MESSAGES" / "kikoe.mo"
        output.parent.mkdir(parents=True)
        subprocess.run(
            [compiler, "--check", "-o", str(output), str(PO_DIR / f"{language}.po")],
            check=True,
        )
    return directory


@pytest.mark.parametrize("language", LANGUAGES)
def test_shipped_catalogs_cover_current_template_without_fuzzy_messages(language: str) -> None:
    comparison = shutil.which("msgcmp")
    attributes = shutil.which("msgattrib")
    if comparison is None or attributes is None:
        pytest.skip("GNU gettext not installed")
    source = PO_DIR / f"{language}.po"
    subprocess.run([comparison, str(source), str(PO_DIR / "kikoe.pot")], check=True)
    for selector in ("--only-fuzzy", "--untranslated"):
        result = subprocess.run(
            [attributes, selector, "--no-obsolete", str(source)],
            capture_output=True,
            text=True,
            check=True,
        )
        assert not result.stdout.strip(), result.stdout


@pytest.mark.parametrize("language", LANGUAGES)
def test_shipped_catalogs_translate_labels_and_all_plural_categories(
    language: str, compiled_catalogs: Path
) -> None:
    catalog = gettext.translation("kikoe", compiled_catalogs, languages=[language])
    assert catalog.gettext("Preferences") != "Preferences"
    assert catalog.gettext("Transcript Text") != "Transcript Text"
    assert catalog.gettext("Transcribing… · {model}").format(model="Example").endswith("Example")
    for count in (0, 1, 2, 3, 5, 11, 21, 101):
        text = catalog.ngettext("{count} recording", "{count} recordings", count)
        assert text not in ("{count} recording", "{count} recordings")
        assert str(count) in text.format(count=count)
        models = catalog.ngettext("{api} API · {count} model", "{api} API · {count} models", count)
        assert "Example" in models.format(api="Example", count=count)


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("zh-Hans", "常规"),
        ("zh_Hans", "常规"),
        ("zh_CN.UTF-8", "常规"),
        ("zh_SG", "常规"),
        ("zh", "常规"),
        ("zh-Hans-TW", "常规"),
        ("zh-Hant", "一般"),
        ("zh_Hant", "一般"),
        ("zh_TW.UTF-8", "一般"),
        ("zh_HK", "一般"),
        ("zh_MO", "一般"),
        ("zh-Hant-CN", "一般"),
        ("pt-BR", "Geral"),
        ("pt-br", "Geral"),
        ("pt_BR.UTF-8", "Geral"),
        ("de_DE.UTF-8", "Allgemein"),
        ("es_MX", "General"),
        ("unknown:zh-Hant:de", "一般"),
        ("zh-Hans:zh-Hant", "常规"),
        ("en:zh-Hant", "一般"),
        ("C:zh-Hant", "General"),
        ("en", "General"),
        ("ar", "عام"),
        ("unknown:ar:de", "عام"),
        ("C:ar", "General"),
    ],
)
def test_setup_loads_locale_aliases_and_ordered_fallbacks(
    language: str,
    expected: str,
    compiled_catalogs: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGUAGE", language)
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setattr(i18n, "LOCALE_DIR", compiled_catalogs)
    monkeypatch.setattr(i18n, "_translation", gettext.NullTranslations())
    i18n.setup()
    assert i18n._("General") == expected
    assert i18n.is_right_to_left() == (expected == "عام")


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"LANGUAGE": "zh-Hant", "LC_ALL": "de", "LC_MESSAGES": "ja", "LANG": "fr"}, "一般"),
        ({"LC_ALL": "zh_CN", "LC_MESSAGES": "ja", "LANG": "fr"}, "常规"),
        ({"LC_MESSAGES": "ja", "LANG": "fr"}, "一般"),
        ({"LANG": "de_DE.UTF-8"}, "Allgemein"),
    ],
)
def test_setup_preserves_environment_precedence(
    environment: dict[str, str],
    expected: str,
    compiled_catalogs: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(i18n, "LOCALE_DIR", compiled_catalogs)
    monkeypatch.setattr(i18n, "_translation", gettext.NullTranslations())
    i18n.setup()
    assert i18n._("General") == expected


@pytest.mark.parametrize(
    ("language", "count", "expected"),
    [
        ("ru", 1, "1 запись"),
        ("ru", 2, "2 записи"),
        ("ru", 5, "5 записей"),
        ("ru", 11, "11 записей"),
        ("ru", 21, "21 запись"),
        ("ar", 0, "0 تسجيل"),
        ("ar", 1, "1 تسجيل"),
        ("ar", 2, "2 تسجيلان"),
        ("ar", 3, "3 تسجيلات"),
        ("ar", 11, "11 تسجيلًا"),
        ("ar", 100, "100 تسجيل"),
        ("fr", 0, "0 enregistrement"),
        ("fr", 2, "2 enregistrements"),
        ("pt_BR", 0, "0 gravação"),
        ("pt_BR", 2, "2 gravações"),
    ],
)
def test_languages_with_distinct_plural_rules_choose_the_expected_form(
    language: str, count: int, expected: str, compiled_catalogs: Path
) -> None:
    catalog = gettext.translation("kikoe", compiled_catalogs, languages=[language])
    result = catalog.ngettext("{count} recording", "{count} recordings", count)
    assert result.format(count=count) == expected
