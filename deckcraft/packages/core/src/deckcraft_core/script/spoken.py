"""Display text != spoken text: '$4.2M', '25%', 'Q3' stay on screen, are spoken naturally."""
from __future__ import annotations
import re

_UNITS = {"k": "thousand", "m": "million", "b": "billion"}
_ABBR = {"e.g.": "for example", "i.e.": "that is", "etc.": "and so on", "approx.": "approximately",
         "hr": "hour", "hrs": "hours", "min": "minutes", "mins": "minutes", "&": "and", "w/": "with"}


def to_spoken(text: str, lexicon: dict[str, str] | None = None) -> str:
    t = text
    t = re.sub(r"\$(\d[\d,]*(?:\.\d+)?)\s?([kKmMbB])\b",
               lambda m: f"{m.group(1).replace(',', '')} {_UNITS[m.group(2).lower()]} dollars", t)
    t = re.sub(r"\$(\d[\d,]*(?:\.\d+)?)", lambda m: f"{m.group(1).replace(',', '')} dollars", t)
    t = re.sub(r"(\d[\d,]*(?:\.\d+)?)\s?%", lambda m: f"{m.group(1).replace(',', '')} percent", t)
    t = re.sub(r"\bQ([1-4])\b", lambda m: f"quarter {m.group(1)}", t)
    t = re.sub(r"\b24/7\b", "twenty four seven", t)
    t = re.sub(r"(\d)-(\d)", r"\1 to \2", t)
    for k, v in _ABBR.items():
        t = re.sub(rf"(?<!\w){re.escape(k)}(?!\w)", v, t, flags=re.I) if k not in ("&",) else t.replace("&", " and ")
    for k, v in (lexicon or {}).items():
        t = re.sub(rf"\b{re.escape(k)}\b", v, t)
    t = t.replace("…", ".").replace("—", ", ").replace("–", " to ")
    return re.sub(r"\s+", " ", t).strip()
