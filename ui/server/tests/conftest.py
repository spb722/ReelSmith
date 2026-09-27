import io
import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]


def png(color=(200, 100, 50), size=(40, 60)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A throwaway repo layout: never the real projects/, styles/ or assets/."""
    (tmp_path / "projects").mkdir()
    (tmp_path / "styles").mkdir()
    for style in ("flat_editorial", "pixel_art"):
        shutil.copy(REPO / "styles" / f"{style}.json", tmp_path / "styles")
    legacy = tmp_path / "assets" / "character"
    legacy.mkdir(parents=True)
    (legacy / "character.png").write_bytes(png((10, 10, 10)))
    (legacy / "character_turnaround.png").write_bytes(png((20, 20, 20)))
    monkeypatch.setenv("REELSMITH_ROOT", str(tmp_path))
    monkeypatch.setenv("REELSMITH_RUN_CMD", f"{sys.executable} -m ui.server.fake_runner")
    monkeypatch.setenv("REELSMITH_COVER_CMD", f"{sys.executable} -m ui.server.fake_runner --cover")
    monkeypatch.setenv("FAKE_RUN_SECONDS", "3")
    monkeypatch.delenv("FAKE_FAIL", raising=False)
    return tmp_path


@pytest.fixture
def client(root):
    from ui.server.app import app
    return TestClient(app)


@pytest.fixture
def project(client):
    """A project with three screenshots."""
    assert client.post("/api/projects", json={"name": "demo"}).status_code == 200
    files = [("files", (f"page{i}.png", png((i * 60, 80, 90)), "image/png")) for i in range(1, 4)]
    assert client.post("/api/projects/demo/screenshots", files=files).status_code == 200
    return "demo"


def wait_for(client, name, states, timeout=40, path="status"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/projects/{name}/{path}").json()
        current = body["status"] if path == "cover" else body
        if current["state"] in states:
            return body
        time.sleep(0.2)
    raise AssertionError(f"still {current['state']} after {timeout}s: {current}")
