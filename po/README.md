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
