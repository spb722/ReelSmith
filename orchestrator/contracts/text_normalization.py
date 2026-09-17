"""Shared text-normalization helper for contract equality/staleness checks
(e.g. narration-script comparisons) -- a public home so contract modules
don't reach into another module's private helper.
"""

from __future__ import annotations

import re


def normalize_text(text: str) -> str:
    text = (
        text.replace("“", '"').replace("”", '"')
        .replace("‘", "'").replace("’", "'")
    )
    return re.sub(r"\s+", " ", text).strip()
