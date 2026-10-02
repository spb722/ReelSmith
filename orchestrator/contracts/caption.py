"""Contract for the Instagram caption, produced by a command of its own.

Like the cover, deliberately outside the reel's stage chain: the caption is
written from the finished narration and nothing in the reel reads it.

The agent returns the caption in parts and `assemble` joins them, so the
order, the blank lines, the signpost emojis and the hashtag line at the very
end are always right. The limits that have a correct answer are checked in
code; how to write each part lives in the `instagram-caption` skill.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

CAPTION_DIR = Path("generated/caption")
CAPTION_FILE = CAPTION_DIR / "caption.txt"
CAPTION_PLAN_FILE = Path("metadata/caption_plan.json")

# Instagram's own limits (checked September 2026).
MAX_CAPTION_CHARS = 2200
MAX_HOOK_CHARS = 125  # about what shows before "…more"
HASHTAG_COUNT = 5  # enforced by Instagram since December 2025
LAST_HASHTAG = "#bookreels"
MIN_SEARCH_PHRASES = 3
MAX_SEARCH_PHRASES = 5

_HASHTAG = re.compile(r"^#[a-z0-9_]+$")


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class CaptionPlanContract(ContractModel):
    produced_by: Literal["caption_agent"] = "caption_agent"
    hook: Annotated[str, Field(min_length=1)]
    concept: Annotated[str, Field(min_length=1)]
    story: Annotated[list[str], Field(min_length=1)]
    turn: Annotated[str, Field(min_length=1)]
    action: Annotated[str, Field(min_length=1)]
    save_share: Annotated[str, Field(min_length=1)]
    question: Annotated[str, Field(min_length=1)]
    hashtags: list[str]
    search_phrases: list[str]

    def assemble(self) -> str:
        return "\n\n".join([
            self.hook.strip(),
            self.concept.strip(),
            "\n".join(line.strip() for line in self.story if line.strip()),
            self.turn.strip(),
            f"✅ {self.action.strip()}",
            f"📌 {self.save_share.strip()}",
            f"💬 {self.question.strip()}",
            " ".join(tag.strip() for tag in self.hashtags),
        ])

    def problems(self) -> list[str]:
        """Every broken limit, in words the agent can act on; empty when valid."""

        text = self.assemble()
        found = caption_problems(text)
        body = text.rsplit("\n\n", 1)[0].lower()
        bad = [tag for tag in self.hashtags if not _HASHTAG.match(tag)]
        if bad:
            found.append(f"hashtags {bad} must be # plus lowercase letters, digits or underscores only")
        if self.hashtags and self.hashtags[-1] != LAST_HASHTAG:
            found.append(f"the last hashtag must be {LAST_HASHTAG}")
        if any("#" in part for part in [self.hook, self.concept, *self.story, self.turn,
                                         self.action, self.save_share, self.question]):
            found.append("put hashtags only in `hashtags`, never inside the text")
        if not self.question.strip().endswith("?"):
            found.append("the question must end with a question mark")
        if not MIN_SEARCH_PHRASES <= len(self.search_phrases) <= MAX_SEARCH_PHRASES:
            found.append(
                f"give {MIN_SEARCH_PHRASES}-{MAX_SEARCH_PHRASES} search_phrases, "
                f"not {len(self.search_phrases)}"
            )
        missing = [p for p in self.search_phrases if p.strip().lower() not in body]
        if missing:
            found.append(f"these search_phrases do not appear in the caption text: {missing}")
        return found


def hashtags_in(text: str) -> list[str]:
    return re.findall(r"#[^\s#]+", text)


def caption_problems(text: str) -> list[str]:
    """Limits any caption must meet, including one edited by hand."""

    found = []
    text = text.strip()
    lines = text.splitlines()
    hook = lines[0] if lines else ""
    if len(hook) > MAX_HOOK_CHARS:
        found.append(f"the first line is {len(hook)} characters; keep it to {MAX_HOOK_CHARS} or fewer")
    if len(text) > MAX_CAPTION_CHARS:
        found.append(f"the caption is {len(text)} characters; the limit is {MAX_CAPTION_CHARS}")
    tags = hashtags_in(text)
    if len(tags) != HASHTAG_COUNT:
        found.append(f"there are {len(tags)} hashtags; use exactly {HASHTAG_COUNT}")
    lowered = [tag.lower() for tag in tags]
    repeats = sorted({tag for tag in lowered if lowered.count(tag) > 1})
    if repeats:
        found.append(f"these hashtags are repeated: {repeats}")
    if LAST_HASHTAG not in lowered:
        found.append(f"{LAST_HASHTAG} is missing")
    if tags and not all(word.startswith("#") for word in lines[-1].split()):
        found.append("put the hashtags together on the last line")
    if "**" in text or "__" in text:
        found.append("remove the markdown (** or __); Instagram shows it as symbols")
    return found
