from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.contracts.production_assets import ProductionAssetEntry
from orchestrator.contracts.stills import StillOutcomeContract, StillResultContract
from orchestrator.contracts.veo import sha256_file, shot_fingerprint
from orchestrator.contracts.visual_plan import Shot, StillMotion
from orchestrator.state.production_assets import load_production_assets, upsert_production_asset


def still_shot(sequence: int = 1) -> Shot:
    return Shot(
        sequence=sequence,
        generation_mode="STILL",
        visual_treatment="USE_EXISTING_ART",
        source_asset_ids=[f"img_{sequence:012d}"],
        shot_goal="Show a quiet reflective beat.",
        frame_composition="Center the illustrated subject.",
        motion_plan="Slow push-in.",
        text_overlay="",
        source_support="The cited screenshot contains this illustration.",
        still_motion=StillMotion(scale_from=1.0, scale_to=1.06, easing="ease"),
    )


def result_for(shot: Shot, *, approved: bool = True) -> StillResultContract:
    image = Path("generated/stills") / f"shot_{shot.sequence:02d}.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(f"still-{shot.sequence}".encode())
    return StillResultContract(
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        source_asset_id=shot.source_asset_ids[0],
        local_image_path=str(image),
        image_sha256=sha256_file(image),
        model="image-model",
        approved=approved,
        qa_summary="Single clean subject, no UI/text." if approved else "",
        cost_usd=0.04,
    )


@pytest.fixture(autouse=True)
def working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def test_still_result_requires_real_materialized_file_and_matching_hash():
    shot = still_shot()
    result = result_for(shot)
    assert result.image_sha256 == sha256_file(Path(result.local_image_path))

    Path(result.local_image_path).write_bytes(b"changed")
    with pytest.raises(ValidationError, match="image_sha256"):
        StillResultContract.model_validate(result.model_dump(mode="json"))


def test_still_result_rejects_path_outside_generated_stills():
    shot = still_shot()
    outside = Path("generated/other") / f"shot_{shot.sequence:02d}.png"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"outside")
    with pytest.raises(ValidationError, match="generated/stills"):
        StillResultContract(
            shot_sequence=shot.sequence,
            shot_fingerprint=shot_fingerprint(shot),
            source_asset_id=shot.source_asset_ids[0],
            local_image_path=str(outside),
            image_sha256=sha256_file(outside),
            model="image-model",
            approved=True,
            qa_summary="wrong directory",
        )


def test_still_result_rejects_non_deterministic_filename():
    shot = still_shot()
    wrong_name = Path("generated/stills") / "not_the_expected_name.png"
    wrong_name.parent.mkdir(parents=True, exist_ok=True)
    wrong_name.write_bytes(b"data")
    with pytest.raises(ValidationError, match="deterministic per-shot"):
        StillResultContract(
            shot_sequence=shot.sequence,
            shot_fingerprint=shot_fingerprint(shot),
            source_asset_id=shot.source_asset_ids[0],
            local_image_path=str(wrong_name),
            image_sha256=sha256_file(wrong_name),
            model="image-model",
            approved=True,
            qa_summary="wrong filename",
        )


def test_still_outcome_success_requires_approved_result_from_agent():
    shot = still_shot()
    result = result_for(shot, approved=False)
    with pytest.raises(ValidationError, match="approved"):
        StillOutcomeContract(
            produced_by="stills_agent",
            status="SUCCESS",
            shot_sequence=shot.sequence,
            shot_fingerprint=shot_fingerprint(shot),
            result=result,
        )


def production_entry(shot: Shot) -> ProductionAssetEntry:
    result = result_for(shot)
    return ProductionAssetEntry.from_still_result(result, source_asset_ids=shot.source_asset_ids)


def test_production_entry_still_branch_enforces_generated_stills_prefix():
    shot = still_shot()
    entry = production_entry(shot)
    assert entry.local_path.startswith("generated/stills/")
    assert entry.asset_type == "still"
    assert entry.generation_mode == "STILL"

    # Same file, wrong prefix: reconstruct the entry payload but point at a
    # path outside generated/stills/ (mirrors the VEO seed-masquerade test).
    outside = Path("generated/other") / f"shot_{shot.sequence:02d}.png"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(Path(entry.local_path).read_bytes())
    payload = entry.model_dump(mode="json")
    payload["local_path"] = str(outside)
    payload["asset_sha256"] = sha256_file(outside)
    with pytest.raises(ValidationError, match="generated/stills"):
        ProductionAssetEntry.model_validate(payload)


def test_from_still_result_rejects_unapproved_result():
    shot = still_shot()
    result = result_for(shot, approved=False)
    with pytest.raises(ValueError, match="approved"):
        ProductionAssetEntry.from_still_result(result, source_asset_ids=shot.source_asset_ids)


def test_keyed_upsert_preserves_veo_and_still_entries_together():
    from orchestrator.contracts.production_assets import ProductionAssetEntry as Entry
    from orchestrator.contracts.veo import VeoResultContract, VeoSeedContract

    def veo_shot(sequence: int) -> Shot:
        return Shot(
            sequence=sequence,
            generation_mode="VEO",
            visual_treatment="AI_VIDEO_CANDIDATE",
            source_asset_ids=[f"img_{sequence:012d}"],
            shot_goal="Show motion.",
            frame_composition="Center the subject.",
            motion_plan="Use restrained motion.",
            text_overlay="",
            source_support="Directly supported.",
            still_motion=None,
        )

    veo = veo_shot(2)
    source = Path("source_images/source.png")
    seed_path = Path("generated/veo_seeds/shot_02_seed.png")
    video_path = Path("generated/veo/shot_02.mp4")
    dump_path = Path("metadata/veo_operations/shot_02.json")
    preview_path = Path("generated/veo/previews/shot_02_01.jpg")
    for p in (source, seed_path, video_path, dump_path, preview_path):
        p.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"source")
    seed_path.write_bytes(b"seed")
    video_path.write_bytes(b"video")
    dump_path.write_text("{}")
    preview_path.write_bytes(b"preview")
    seed = VeoSeedContract(
        shot_sequence=veo.sequence,
        shot_fingerprint=shot_fingerprint(veo),
        source_asset_id=veo.source_asset_ids[0],
        source_image_path=str(source),
        source_image_sha256=sha256_file(source),
        local_path=str(seed_path),
        seed_sha256=sha256_file(seed_path),
        prompt="Recompose.",
        model="image-model",
        approved=True,
        qa_summary="Clean.",
        cost_usd=0.04,
    )
    veo_result = VeoResultContract(
        shot_sequence=veo.sequence,
        shot_fingerprint=shot_fingerprint(veo),
        seed=seed,
        local_video_path=str(video_path),
        video_sha256=sha256_file(video_path),
        operation_dump_path=str(dump_path),
        preview_paths=[str(preview_path)],
        model="veo-model",
        approved=True,
        qa_summary="Motion approved.",
        cost_usd=1.2,
    )
    veo_entry = Entry.from_veo_result(veo_result, source_asset_ids=veo.source_asset_ids)

    still = still_shot(1)
    still_entry = production_entry(still)

    path = Path("metadata/production_assets.json")
    upsert_production_asset(still_entry, path=path)
    updated = upsert_production_asset(veo_entry, path=path)

    assert set(updated.shots) == {"1", "2"}
    loaded = load_production_assets(path)
    assert loaded.shots["1"].generation_mode == "STILL"
    assert loaded.shots["2"].generation_mode == "VEO"
