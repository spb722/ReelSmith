from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator import workspace
from orchestrator.workspace import ProjectError


def make_project(root: Path, name: str, *, with_screenshots: bool = True) -> Path:
    project = root / name
    source = project / workspace.SOURCE_IMAGES_DIR_NAME
    source.mkdir(parents=True)
    if with_screenshots:
        (source / "shot.png").write_bytes(b"not really a png, but a file")
    return project


@pytest.fixture
def projects(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(workspace, "PROJECTS_DIR", root)
    return root


# --- finding a project -----------------------------------------------------


def test_a_project_with_screenshots_resolves(projects):
    make_project(projects, "fear-mask")
    assert workspace.resolve_project("fear-mask") == projects / "fear-mask"


def test_surrounding_whitespace_and_slashes_are_tolerated(projects):
    make_project(projects, "fear-mask")
    assert workspace.resolve_project("  fear-mask/ ") == projects / "fear-mask"


def test_an_unknown_project_lists_the_ones_that_exist(projects):
    """A typo must not resolve to something plausible: writing into the wrong
    project would overwrite a finished reel."""
    make_project(projects, "fear-mask")
    make_project(projects, "next-book")
    with pytest.raises(ProjectError) as exc:
        workspace.resolve_project("fear-msak")
    assert "fear-mask" in str(exc.value) and "next-book" in str(exc.value)


def test_no_projects_at_all_says_how_to_make_one(projects):
    with pytest.raises(ProjectError, match="mkdir -p projects/"):
        workspace.resolve_project("anything")


def test_an_empty_name_is_refused(projects):
    with pytest.raises(ProjectError, match="project name is required"):
        workspace.resolve_project("   ")


def test_a_path_is_not_a_project_name(projects):
    """Guards against ../ escaping the projects directory."""
    make_project(projects, "fear-mask")
    with pytest.raises(ProjectError, match="single folder name"):
        workspace.resolve_project("../../etc")


def test_a_project_without_a_source_images_folder_is_refused(projects):
    (projects / "empty").mkdir()
    with pytest.raises(ProjectError, match="source_images"):
        workspace.resolve_project("empty")


def test_a_project_with_no_screenshots_yet_still_resolves(projects):
    """Emptiness is `ingest`'s call, and it reports it clearly before any paid
    work. Enforcing it here as well would be two rules that can disagree."""
    make_project(projects, "blank", with_screenshots=False)
    assert workspace.resolve_project("blank") == projects / "blank"


def test_listing_projects_ignores_stray_files(projects):
    make_project(projects, "b-reel")
    make_project(projects, "a-reel")
    (projects / ".DS_Store").write_text("junk")
    assert workspace.list_projects() == ["a-reel", "b-reel"]


# --- entering one ----------------------------------------------------------


def test_entering_a_project_makes_its_relative_paths_live(projects, monkeypatch):
    """The whole mechanism: after the chdir, `metadata/...` means this reel's."""
    project = make_project(projects, "fear-mask")
    (project / "metadata").mkdir()
    (project / "metadata" / "visual_plan.json").write_text("{}")

    monkeypatch.chdir(projects)
    assert not Path("metadata/visual_plan.json").is_file()

    workspace.enter_project(project)
    assert Path("metadata/visual_plan.json").is_file()
    assert Path.cwd() == project.resolve()


def test_two_projects_cannot_see_each_other(projects, monkeypatch):
    """The point of the whole change: reel two must not read reel one."""
    first = make_project(projects, "first")
    (first / "metadata").mkdir()
    (first / "metadata" / "visual_plan.json").write_text('{"reel": 1}')
    second = make_project(projects, "second")

    workspace.enter_project(second)
    assert not Path("metadata/visual_plan.json").is_file()


# --- shared vs per-project assets ------------------------------------------


def test_the_repo_root_is_absolute_and_holds_the_shared_pieces():
    """It is derived from __file__, not the cwd, because the cwd moves."""
    assert workspace.REPO_ROOT.is_absolute()
    assert (workspace.REPO_ROOT / "orchestrator").is_dir()
    assert workspace.repo_path("remotion").is_absolute()


def test_a_project_asset_overrides_the_shared_one(projects, monkeypatch, tmp_path):
    project = make_project(projects, "fear-mask")
    own = project / "assets" / "character" / "character.png"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"this project's own character")

    shared_root = tmp_path / "repo"
    (shared_root / "assets" / "character").mkdir(parents=True)
    (shared_root / "assets" / "character" / "character.png").write_bytes(b"the shared one")
    monkeypatch.setattr(workspace, "REPO_ROOT", shared_root)

    workspace.enter_project(project)
    resolved = workspace.resolve_shared_asset("assets/character/character.png")
    assert resolved is not None
    assert resolved.read_bytes() == b"this project's own character"


def test_a_project_without_its_own_asset_gets_the_shared_one(projects, monkeypatch, tmp_path):
    project = make_project(projects, "fear-mask")

    shared_root = tmp_path / "repo"
    (shared_root / "assets" / "character").mkdir(parents=True)
    (shared_root / "assets" / "character" / "character.png").write_bytes(b"the shared one")
    monkeypatch.setattr(workspace, "REPO_ROOT", shared_root)

    workspace.enter_project(project)
    resolved = workspace.resolve_shared_asset("assets/character/character.png")
    assert resolved is not None
    assert resolved.read_bytes() == b"the shared one"


def test_no_asset_anywhere_means_the_feature_is_off(projects, monkeypatch, tmp_path):
    project = make_project(projects, "fear-mask")
    monkeypatch.setattr(workspace, "REPO_ROOT", tmp_path / "repo")
    workspace.enter_project(project)
    assert workspace.resolve_shared_asset("assets/character/character.png") is None


def test_an_empty_setting_means_the_feature_is_off():
    assert workspace.resolve_shared_asset("") is None
    assert workspace.resolve_shared_asset("   ") is None


def test_an_absolute_asset_path_is_used_as_given(tmp_path):
    sheet = tmp_path / "elsewhere.png"
    sheet.write_bytes(b"x")
    assert workspace.resolve_shared_asset(str(sheet)) == sheet
    assert workspace.resolve_shared_asset(str(tmp_path / "absent.png")) is None
