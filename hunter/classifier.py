"""
Venue classification logic for dblp XML records.
"""
from typing import Optional

try:
    from .config import CONF
except (ImportError, ValueError):
    from config import CONF


def classify(el, key: str, conf: Optional[dict] = None) -> Optional[str]:
    """Return venue acronym for a record, or None if not an A* paper."""
    if conf is None:
        conf = CONF

    prefix = "/".join(key.split("/")[:2])
    if el.tag == "inproceedings":
        venue = conf.get(prefix)
        if venue == "ICAPS" and el.findtext("booktitle") != "ICAPS":
            return None
        return venue

    # <article>: venues published in journals
    num = el.findtext("number")
    if prefix == "journals/pvldb":
        return "VLDB"
    if prefix == "journals/pacmpl":       # POPL / PLDI issues
        return num if num in ("POPL", "PLDI") else None
    if prefix == "journals/pacmse":       # FSE issues (skip ISSTA)
        return "FSE" if num == "FSE" else None
    if prefix == "journals/pacmmod":      # SIGMOD (PODS papers are mixed in)
        return "SIGMOD"
    if prefix == "journals/pomacs":       # SIGMETRICS
        return "SIGMETRICS"
    if prefix == "journals/tog":          # issue 4 = SIGGRAPH, 6 = SIGGRAPH Asia
        return {"4": "SIGGRAPH", "6": "SiggraphA"}.get(num)
    return None
