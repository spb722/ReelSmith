from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator import migrate, workspace
from orchestrator.workspace import ProjectError


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A fake repo root holding a pre-projects reel."""
    for name in ("source_images", "metadata", "generated", "audio", "orchestrator_runs"):
        (tmp_path / name).mkdir()
    (tmp_path / "metadata" / "visual_plan.json").write_text('{"reel": 1}')
    (tmp_path / "source_images" / "shot.png").write_bytes(b"x")
    (tmp_path / "remotion" / "out").mkdir(parents=True)
    (tmp_path / "remotion" / "out" / "book_reel.mp4").write_bytes(b"mp4")

    monkeypatch.setattr(workspace, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(workspace, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(migrate, "PROJECTS_DIR", tmp_path / "projects")
    return tmp_path


def test_every_per_reel_folder_moves_into_the_project(repo):
    assert migrate.main(["fear-mask"]) == 0
    project = repo / "projects" / "fear-mask"

    for name in ("source_images", "metadata", "generated", "audio", "orchestrator_runs"):
        assert (project / name).is_dir(), name
        assert not (repo / name).exists(), f"{name} should have moved, not been copied"
    assert (project / "metadata" / "visual_plan.json").read_text() == '{"reel": 1}'


def test_the_rendered_reel_is_copied_not_moved(repo):
    """remotion/out/ is shared scratch the next render overwrites, so the
    project takes its own copy while the renderer keeps working."""
    assert migrate.main(["fear-mask"]) == 0
    assert (repo / "projects" / "fear-mask" / "book_reel.mp4").read_bytes() == b"mp4"
    assert (repo / "remotion" / "out" / "book_reel.mp4").is_file()


def test_the_shared_renderer_is_left_alone(repo):
    assert migrate.main(["fear-mask"]) == 0
    assert (repo / "remotion").is_dir()
    assert not (repo / "projects" / "fear-mask" / "remotion").exists()


def test_it_refuses_to_overwrite_an_existing_reel(repo):
    """The whole point is that finished work stops being deletable."""
    existing = repo / "projects" / "fear-mask"
    existing.mkdir(parents=True)
    (existing / "metadata").mkdir()

    assert migrate.main(["fear-mask"]) == 1
    # Nothing moved.
    assert (repo / "metadata" / "visual_plan.json").is_file()


def test_an_empty_target_folder_is_fine(repo):
    (repo / "projects" / "fear-mask").mkdir(parents=True)
    assert migrate.main(["fear-mask"]) == 0
    assert (repo / "projects" / "fear-mask" / "metadata").is_dir()


def test_a_path_is_not_a_project_name(repo):
    with pytest.raises(ProjectError, match="single folder name"):
        migrate.plan_migration("../escape")


def test_nothing_to_migrate_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(workspace, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(migrate, "PROJECTS_DIR", tmp_path / "projects")
    with pytest.raises(ProjectError, match="Nothing to migrate"):
        migrate.plan_migration("fear-mask")
