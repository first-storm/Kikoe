# Translations

Kikoe uses GNU gettext. Source strings are English.

## Add a language

1. `uv run scripts/build_ui.py && uv run scripts/i18n.py update` to refresh `kikoe.pot`.
2. `msginit --input=po/kikoe.pot --output-file=po/<lang>.po --locale=<lang>`
3. Add `<lang>` to `po/LINGUAS`, translate `po/<lang>.po`.
4. `uv run scripts/i18n.py compile`, then run Kikoe with `LANGUAGE=<lang>` to test.

## Notes for developers

- Wrap user-visible Python strings in `_()` from `kikoe.i18n`; use `ngettext` for counts.
- In Blueprint, write `_("...")`; add context for ambiguous words with `C_("context", "...")`.
- Leave a `# Translators:` comment above a Python string that needs explanation.
- Do not build sentences by concatenating translated fragments; use one string with
  `{placeholders}` and `str.format`.


## Included languages and template translation

All included catalogs cover the current source messages, including plural counts and error text.

| Language | Language tag | Catalog |
| --- | --- | --- |
| Simplified Chinese | `zh-Hans` | `zh_Hans` |
| Traditional Chinese | `zh-Hant` | `zh_Hant` |
| Spanish | `es` | `es` |
| French | `fr` | `fr` |
| German | `de` | `de` |
| Japanese | `ja` | `ja` |
| Korean | `ko` | `ko` |
| Portuguese (Brazil) | `pt-BR` | `pt_BR` |
| Russian | `ru` | `ru` |
| Italian | `it` | `it` |
| Arabic | `ar` | `ar` |
| Hindi | `hi` | `hi` |
| Indonesian | `id` | `id` |
| Turkish | `tr` | `tr` |

Compile catalogs before running or packaging; `.mo` files are generated and not committed.
Use `LANGUAGE=zh-Hans uv run kikoe` to inspect the Simplified Chinese interface.
Interface language follows the system; the transcription language is an independent
preference and defaults to automatic detection.

Both BCP 47 language tags and gettext catalog names work. `zh_CN` and `zh_SG` select
Simplified Chinese; `zh_TW`, `zh_HK` and `zh_MO` select Traditional Chinese. An explicit
`Hans` or `Hant` script takes priority over the region. Bare `zh` selects Simplified
Chinese. Encoding suffixes such as `.UTF-8` are supported. Other regional locales
use gettext's normal language fallback; Brazilian Portuguese remains specific to `pt_BR`.
Language sources retain gettext's precedence: `LANGUAGE`, `LC_ALL`, `LC_MESSAGES`,
`LANG`, then the system locale when none is set. Colon-separated choices are tried
in order, with untranslated messages falling back to English. Arabic uses GTK's
standard right-to-left layout based on the loaded catalog.

Run `uv run pytest tests/test_i18n.py` to check completeness, compilation, aliases,
fallbacks and plural handling. Review translations before clearing `fuzzy` flags;
keep named placeholders and shortcut mnemonics intact.

`kikoe.i18n.translated_ui` uses Python gettext for marked XML properties and contexts,
caches the translated template by content, and removes GTK translation markers. Python
and Blueprint therefore share the same catalog even without native gettext bindings.
New locales and rebuilt templates get separate cache entries; originals stay unchanged.
