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


## Included language and template translation

`zh_CN` covers all current source messages, including plural counts and error text.
Compile catalogs before running or packaging; `.mo` files are generated and not committed.
Use `LANGUAGE=zh_CN uv run kikoe` to inspect the Chinese interface; transcription language
is an independent preference and defaults to automatic detection.

`kikoe.i18n.translated_ui` uses Python gettext for marked XML properties and contexts,
caches the translated template by content, and removes GTK translation markers. Python
and Blueprint therefore share the same catalog even without native gettext bindings.
New locales and rebuilt templates get separate cache entries; originals stay unchanged.
