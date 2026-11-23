"""Multilingual support (feature-flagged): language -> TTS language/voice, scene translation with
number-preservation checks, and which verifier checks are English-only."""
from __future__ import annotations
import re
from collections import Counter

from pydantic import BaseModel

from .scenes import Base, parse_scene

# language -> (kokoro lang code, default voice, English name)
LANGS = {
    "en": ("en-us", "af_heart", "English"),
    "en-gb": ("en-gb", "bf_emma", "British English"),
    "es": ("es", "ef_dora", "Spanish"),
    "fr": ("fr-fr", "ff_siwis", "French"),
    "it": ("it", "if_sara", "Italian"),
    "pt": ("pt-br", "pf_dora", "Portuguese"),
}
SKIP_KEYS = {"kind", "claim_ids", "image_sha", "image_url", "answer_index", "notes"}


def tts_params(language: str) -> tuple[str, str]:
    lang, voice, _ = LANGS.get(language, LANGS["en"])
    return lang, voice


def is_english(language: str) -> bool:
    return language.split("-")[0] == "en"


def _collect(node, path=()):
    """Yield (path, string) for every translatable string in a scene dict."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k not in SKIP_KEYS:
                yield from _collect(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _collect(v, path + (i,))
    elif isinstance(node, str) and node.strip():
        yield path, node


def _set(root, path, val):
    for p in path[:-1]:
        root = root[p]
    root[path[-1]] = val


def digits(text: str) -> Counter:
    return Counter(re.findall(r"\d+", text))


class Translated(BaseModel):
    lines: list[str]


def translate_strings(llm, strings: list[str], language: str) -> list[str]:
    name = LANGS[language][2]
    msgs = [{"role": "system", "content": (
        f"Translate each line of workplace-policy training text into {name}. Keep every number, date, amount, "
        "name and email exactly as written. Keep must / must not strength. Keep similar length. "
        'Return JSON {"lines": [...]} with exactly the same number of lines, in the same order.')},
        {"role": "user", "content": "\n".join(f"{i + 1}. {s}" for i, s in enumerate(strings))}]
    out = llm.generate(msgs, Translated, temperature=0.1).lines
    if len(out) != len(strings):
        raise ValueError(f"translation returned {len(out)} lines for {len(strings)}")
    for a, b in zip(strings, out):
        if digits(a) != digits(b):
            raise ValueError(f"numbers changed in translation: {a!r} -> {b!r}")
    return [re.sub(r"^\d+\.\s*", "", o).strip() for o in out]


def translate_scene(llm, scene: Base, language: str) -> Base:
    """Translate every visible string; the result must still satisfy the scene schema (capacity)."""
    data = scene.model_dump()
    items = list(_collect(data))
    new = translate_strings(llm, [s for _, s in items], language)
    for (path, _), t in zip(items, new):
        _set(data, path, t)
    return parse_scene(data)


def translate_beats(llm, beats: list[str], language: str) -> list[str]:
    return translate_strings(llm, beats, language)
