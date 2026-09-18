from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.contracts.production_assets import ProductionAssetEntry
from orchestrator.contracts.veo import VeoSeedContract, sha256_file, shot_fingerprint
from orchestrator.contracts.visual_plan import Shot
from orchestrator.state.production_assets import load_production_assets, upsert_production_asset


def veo_shot(sequence: int = 1) -> Shot:
    return Shot(
        sequence=sequence,
        generation_mode="VEO",
        visual_treatment="AI_VIDEO_CANDIDATE",
        source_asset_ids=[f"img_{sequence:012d}"],
        shot_goal="Show a quiet emotional beat.",
        frame_composition="Center the illustrated subject.",
        motion_plan="Use restrained environmental motion.",
        text_overlay="",
        source_support="The cited screenshot contains this illustration.",
    )


def seed_for(shot: Shot, *, approved: bool = True) -> VeoSeedContract:
    source = Path("source_images") / f"source_{shot.sequence}.png"
    seed = Path("generated/veo_seeds") / f"shot_{shot.sequence:02d}_seed.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    seed.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(f"source-{shot.sequence}".encode())
    seed.write_bytes(f"seed-{shot.sequence}".encode())
    return VeoSeedContract(
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        source_asset_id=shot.source_asset_ids[0],
        source_image_path=str(source),
        source_image_sha256=sha256_file(source),
        local_path=str(seed),
        seed_sha256=sha256_file(seed),
        prompt="Create a clean recomposed frame.",
        model="image-model",
        approved=approved,
        qa_summary="Clean semantic match." if approved else "",
        cost_usd=0.04,
    )


@pytest.fixture(autouse=True)
def working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def test_seed_contract_requires_real_recomposed_file_and_matching_hash():
    shot = veo_shot()
    seed = seed_for(shot)
    seed.validate_for_shot(shot)

    Path(seed.local_path).write_bytes(b"changed")
    with pytest.raises(ValueError, match="seed_sha256"):
        seed.validate_for_shot(shot)


def test_source_image_can_never_masquerade_as_seed():
    shot = veo_shot()
    source = Path("source_images/raw.png")
    source.parent.mkdir(parents=True)
    source.write_bytes(b"raw")
    with pytest.raises(ValidationError, match="generated/veo_seeds"):
        VeoSeedContract(
            shot_sequence=1,
            shot_fingerprint=shot_fingerprint(shot),
            source_asset_id=shot.source_asset_ids[0],
            source_image_path=str(source),
            source_image_sha256=sha256_file(source),
            local_path=str(source),
            seed_sha256=sha256_file(source),
            prompt="unsafe",
            model="image-model",
            approved=True,
            qa_summary="wrong",
        )


def production_entry(shot: Shot) -> ProductionAssetEntry:
    seed = seed_for(shot)
    video = Path("generated/veo") / f"shot_{shot.sequence:02d}.mp4"
    dump = Path("metadata/veo_operations") / f"shot_{shot.sequence:02d}.json"
    preview = Path("generated/veo/previews") / f"shot_{shot.sequence:02d}_01.jpg"
    video.parent.mkdir(parents=True, exist_ok=True)
    dump.parent.mkdir(parents=True, exist_ok=True)
    preview.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video")
    dump.write_text("{}")
    preview.write_bytes(b"preview")
    return ProductionAssetEntry(
        shot_sequence=shot.sequence,
        shot_fingerprint=shot_fingerprint(shot),
        generation_mode="VEO",
        asset_type="video",
        local_path=str(video),
        asset_sha256=sha256_file(video),
        source_asset_ids=shot.source_asset_ids,
        seed_path=seed.local_path,
        seed_sha256=seed.seed_sha256,
        generation_model="veo-model",
        gcs_uri=f"gs://bucket/shot-{shot.sequence}.mp4",
        operation_name=f"operations/{shot.sequence}",
        operation_dump_path=str(dump),
        preview_paths=[str(preview)],
        qa_summary="Motion and composition approved.",
        cost_usd=1.24,
    )


def test_atomic_keyed_upsert_preserves_previous_shots_and_rejects_other_writers():
    path = Path("metadata/production_assets.json")
    first = production_entry(veo_shot(1))
    second = production_entry(veo_shot(2))

    upsert_production_asset(first, path=path)
    updated = upsert_production_asset(second, path=path)

    assert list(updated.shots) == ["1", "2"]
    assert load_production_assets(path).shots["1"].local_path == first.local_path
    with pytest.raises(ValueError, match="Only the orchestrator"):
        upsert_production_asset(first, path=path, writer="agent")  # type: ignore[arg-type]
