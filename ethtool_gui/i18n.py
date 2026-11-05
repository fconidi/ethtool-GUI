"""Translations for every string shown to the user (English and Italian)."""

import os

from .i18n_gui import CATALOG

DEFAULT_LANGUAGE = "en"


def current_language() -> str:
    """Return the language code from the environment, or the default."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        for part in value.split(":"):
            code = part.split(".")[0].split("_")[0].split("@")[0].lower()
            if code in CATALOG:
                return code
            if code and code not in ("c", "posix"):
                break
    return DEFAULT_LANGUAGE


def translate(key: str, lang: str | None = None, **fields) -> str:
    """Return the text for key in lang, falling back to English, then to the key."""
    if lang is None:
        lang = current_language()
    text = CATALOG.get(lang, CATALOG[DEFAULT_LANGUAGE]).get(key)
    if text is None:
        text = CATALOG[DEFAULT_LANGUAGE].get(key, key)
    return text.format(**fields) if fields else text
