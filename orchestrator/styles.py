"""Named art styles for SOURCE_MODE=content reels.

With no screenshot artwork to copy, a reel's look comes from one of the JSON
files in `styles/` at the repo root, picked with `--style <name>` (default
`flat_editorial`). The orchestrator writes the style's text into the visual
plan itself, so every image request carries it word for word and no agent
can rewrite it.
"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from orchestrator.workspace import REPO_ROOT

STYLES_DIR = REPO_ROOT / "styles"
DEFAULT_STYLE = "flat_editorial"

Text = Annotated[str, Field(min_length=1)]


class StyleError(Exception):
    """A style name that has no valid file behind it."""


class Style(BaseModel):
    # extra="forbid": a misspelt field in a hand-edited file is an error, not
    # a silently ignored instruction.
    model_config = ConfigDict(strict=True, extra="forbid")

    name: Text
    description: Text
    palette: Annotated[list[Text], Field(min_length=1)]
    lighting: Text
    mood: Text
    avoid: list[Text]
    # Shown by the studio UI only; never part of the image prompt.
    display_name: Text | None = None

    def guide(self) -> str:
        """The style as one paragraph, as it goes into every image request.

        Never mentions the recurring character: how they look comes from the
        reference sheet alone.
        """

        text = (
            f"{self.description.strip().rstrip('.')}. "
            f"Palette: {', '.join(self.palette)}. "
            f"Lighting: {self.lighting.strip().rstrip('.')}. "
            f"Mood: {self.mood.strip().rstrip('.')}."
        )
        if self.avoid:
            text += f" Avoid: {', '.join(self.avoid)}."
        return text


def available_styles() -> list[str]:
    return sorted(path.stem for path in STYLES_DIR.glob("*.json"))


def load_style(name: str) -> Style:
    path = STYLES_DIR / f"{name}.json"
    if not path.is_file():
        raise StyleError(
            f"No style named {name!r}. Available: {', '.join(available_styles()) or 'none'} "
            f"(files in {STYLES_DIR})"
        )
    try:
        style = Style.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise StyleError(f"Style file {path} is not valid: {exc}") from exc
    if style.name != name:
        raise StyleError(f"Style file {path} says its name is {style.name!r}; it must be {name!r}")
    return style
