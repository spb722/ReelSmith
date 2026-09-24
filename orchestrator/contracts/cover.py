"""Contract for the Instagram cover, produced by a command of its own.

Deliberately outside the reel's stage chain: the cover is made *after* the
operator has watched the finished MP4 and decided it is worth posting, so it
is neither a dependency of delivery nor a thing that should re-run when the
reel does.

Nothing here names a particular story. The plan cites a shot sequence and a
hook drawn from that run's own narration, so the next reel -- different book,
different arc -- goes through the identical code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

COVER_DIR = Path("generated/cover")
COVER_PLAN_FILE = Path("metadata/cover_plan.json")

# Instagram serves the reel at 9:16 but crops the profile grid to a portrait
# slice of it. A hook that sits outside that slice survives in the player and
# vanishes from the grid, which is where most people first meet the post.
COVER_WIDTH = 1080
COVER_HEIGHT = 1920
GRID_CROP_ASPECT_RATIO = 4 / 5

# Six words is what stays readable at the size a grid thumbnail is actually
# rendered. Longer hooks are not rejected as invalid -- they are just poor --
# so this is the agent's brief, and the ceiling below only catches runaways.
MAX_HOOK_WORDS = 10


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class CoverPlanContract(ContractModel):
    produced_by: Literal["cover_agent"] = "cover_agent"
    base_shot_sequence: Annotated[int, Field(ge=1)]
    hook_text: Annotated[str, Field(min_length=1)]
    art_direction: Annotated[str, Field(min_length=1)]
    rationale: Annotated[str, Field(min_length=1)]

    def hook_word_count(self) -> int:
        return len(self.hook_text.split())
