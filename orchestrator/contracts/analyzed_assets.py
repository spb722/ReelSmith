"""Screenshot-understanding contract, compatible with story_director's input.

The eight analysis fields mirror the legacy shape without importing its
Gemini schema or client. Provenance stays outside ``analysis`` as before.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    # Legacy manifests include additional provenance (model, timestamp, etc.).
    # Accept those additive fields without weakening validation of known fields.
    model_config = ConfigDict(strict=True, extra="ignore")


Coordinate = Annotated[int, Field(ge=0, le=1000)]


class Region(ContractModel):
    x_min: Coordinate
    y_min: Coordinate
    x_max: Coordinate
    y_max: Coordinate

    @model_validator(mode="after")
    def ordered(self) -> Region:
        if self.x_min > self.x_max or self.y_min > self.y_max:
            raise ValueError("Region minimum must not exceed maximum")
        return self


class VerbatimBlock(ContractModel):
    reading_order: int
    block_type: Literal["HEADING", "BODY", "QUOTE", "STORY_LABEL", "UI", "OTHER"]
    text: str
    region: Region


class SourceText(ContractModel):
    heading: str
    body_text: str
    prominent_quote: str
    other_story_text: list[str]
    ui_text: list[str]
    verbatim_blocks: list[VerbatimBlock]
    transcription_confidence: Annotated[float, Field(ge=0, le=1)]


class VisualSubject(ContractModel):
    subject_type: Literal[
        "REAL_PERSON", "ILLUSTRATED_PERSON", "CARTOON_CHARACTER", "OBJECT",
        "LANDMARK", "ENVIRONMENT", "SYMBOL", "OTHER",
    ]
    description: str
    region: Region


class Visual(ContractModel):
    description: str
    main_elements: list[str]
    visual_subjects: list[VisualSubject]
    real_people_visible: bool
    illustrated_or_cartoon_figures_visible: bool
    figure_descriptions: list[str]
    environment: str
    important_actions: list[str]
    composition_notes: str


class SemanticSummary(ContractModel):
    core_idea: str
    concepts: list[str]
    emotional_tone: list[str]
    requires_external_context: bool
    context_needed: list[str]


class NamedEntity(ContractModel):
    name: str
    entity_type: str
    evidence: str


class UIRegion(ContractModel):
    description: str
    region: Region


class Production(ContractModel):
    contains_app_ui: bool
    story_art_region_present: bool
    story_art_region: Region
    story_text_region: Region
    ui_regions: list[UIRegion]
    story_art_description: str
    vertical_video_suitability: Literal["high", "medium", "low"]
    recommended_crop_strategy: Literal[
        "USE_FULL_FRAME", "CROP_TO_ART", "CROP_TO_TEXT_AND_ART",
        "RECOMPOSE", "NEEDS_GENERATION",
    ]
    safe_to_crop_ui_without_losing_story: bool
    suggested_motion: list[str]
    visual_cleanup_needed: list[str]


class AssetAnalysis(ContractModel):
    content_type: str
    source_text: SourceText
    visual: Visual
    semantic_summary: SemanticSummary
    named_entities: list[NamedEntity]
    possible_story_roles: list[Literal[
        "HOOK", "SETUP", "CONTEXT", "EXAMPLE", "CONFLICT", "BUILDUP",
        "REVERSAL", "REVEAL", "EXPLANATION", "PAYOFF", "REFLECTION", "ENDING", "OTHER",
    ]]
    production: Production
    uncertainties: list[str]


class AnalyzedAsset(ContractModel):
    asset_id: Annotated[str, Field(pattern=r"^img_[0-9a-f]{12}$")]
    source_path: Annotated[str, Field(min_length=1)]
    original_filename: Annotated[str, Field(min_length=1)]
    analysis: AssetAnalysis


class AnalyzedAssetsContract(ContractModel):
    # Required, no default: a legacy `analyze_assets.py` (Gemini) manifest never
    # has this field, so it fails validation here rather than silently satisfying
    # the single-stage resume check (AD-1 -- resume must never launder Gemini-era
    # output as if `asset_analyst` had produced it).
    produced_by: Literal["asset_analyst"]
    assets: Annotated[list[AnalyzedAsset], Field(min_length=1)]

    def validate_sources(self, ingested_assets: list[dict]) -> None:
        """Require complete coverage of this invocation's screenshots.

        A schema-valid file for a different reel must not become a resume hit.
        Hash-derived IDs also invalidate results after an image changes in place.
        Counter preserves duplicate-file multiplicity; array order is irrelevant.
        """
        expected = Counter(
            (a["asset_id"], str(Path(a["source_path"]).resolve()), a["original_filename"])
            for a in ingested_assets
        )
        actual = Counter(
            (a.asset_id, str(Path(a.source_path).resolve()), a.original_filename)
            for a in self.assets
        )
        if actual != expected:
            raise ValueError("Analyzed assets must cover every ingested screenshot exactly once with matching provenance")
