"""API tests against a temp projects root, with the fake runner standing in for the pipeline."""

import json
import time

from ui.server.tests.conftest import png, wait_for


# ---------------------------------------------------------------- projects

def test_create_project_and_names(client):
    assert client.post("/api/projects", json={"name": "bad name!"}).status_code == 400
    r = client.post("/api/projects", json={"name": "alpha"})
    assert r.status_code == 200 and r.json()["status"] == "draft"
    assert client.post("/api/projects", json={"name": "alpha"}).status_code == 409
    assert client.get("/api/projects/nope").status_code == 404
    names = [p["name"] for p in client.get("/api/projects").json()]
    assert names == ["alpha"]


def test_screenshots_upload_reorder_delete(client, project, root):
    shots = client.get(f"/api/projects/{project}/screenshots").json()
    assert [s["file"][:8] for s in shots] == ["01_page1", "02_page2", "03_page3"]
    files = [("files", ("dup.png", png((60, 80, 90)), "image/png")),  # same bytes as page1
             ("files", ("notes.txt", b"hello", "text/plain"))]
    body = client.post(f"/api/projects/{project}/screenshots", files=files).json()
    assert len(body["screenshots"]) == 3
    assert {r["file"] for r in body["rejected"]} == {"dup.png", "notes.txt"}
    order = [s["file"] for s in shots][::-1]
    reordered = client.put(f"/api/projects/{project}/screenshots/order", json={"order": order}).json()
    # The last picture is now first, and every file is renumbered in the new order.
    first_before = (root / "projects" / project / "source_images" / reordered[0]["file"]).read_bytes()
    assert reordered[0]["file"].startswith("01_") and "page3" in reordered[0]["file"]
    assert first_before == png((180, 80, 90))
    assert client.put(f"/api/projects/{project}/screenshots/order", json={"order": order[:1]}).status_code == 400
    left = client.delete(f"/api/projects/{project}/screenshots/{reordered[0]['file']}").json()
    assert [s["file"][:3] for s in left] == ["01_", "02_"]


def test_duplicate_and_delete_project(client, project):
    r = client.post(f"/api/projects/{project}/duplicate", json={"new_name": "copy"})
    assert r.status_code == 200 and r.json()["screenshot_count"] == 3
    assert client.delete("/api/projects/copy").json() == {"ok": True}
    assert client.get("/api/projects/copy").status_code == 404


# ---------------------------------------------------------------- settings

def test_settings_validation_and_defaults(client, project):
    body = client.get(f"/api/projects/{project}/settings").json()
    settings = body["settings"]
    assert settings == body["defaults"] and settings["character_id"] == "default"
    for field, value, words in (("max_budget_usd", -1, "greater than zero"),
                                ("source_mode", "video", "Where the pictures come from"),
                                ("shot_concurrency", 1.5, "whole number"),
                                ("character_id", "ghost", "not in the character pool")):
        r = client.put(f"/api/projects/{project}/settings", json={**settings, field: value})
        assert r.status_code == 400 and words in r.json()["detail"], r.json()
    r = client.put(f"/api/projects/{project}/settings", json={**settings, "image_fallback_provider": "gemini"})
    assert r.status_code == 400
    changed = {**settings, "max_budget_usd": 12.5, "character_id": None,
               "max_attempts": {**settings["max_attempts"], "stills_agent": 5}}
    saved = client.put(f"/api/projects/{project}/settings", json=changed).json()["settings"]
    assert saved["max_budget_usd"] == 12.5 and saved["character_id"] is None
    # The raw config shows and syncs the attempt limits.
    config = client.get(f"/api/projects/{project}/config").json()
    assert json.loads(config["text"])["limits"]["max_attempts"]["stills_agent"] == 5
    text = json.dumps({"limits": {"max_attempts": {"stills_agent": 7}}, "usage": {"budget_spent_usd": 1}})
    assert client.put(f"/api/projects/{project}/config", json={"text": "{nope"}).status_code == 400
    assert client.put(f"/api/projects/{project}/config", json={"text": text}).status_code == 200
    assert client.get(f"/api/projects/{project}/settings").json()["settings"]["max_attempts"]["stills_agent"] == 7
    # Defaults and presets.
    defaults = client.get("/api/defaults").json()
    assert client.put("/api/defaults", json={**defaults, "voice": "Kore"}).json()["voice"] == "Kore"
    assert client.post("/api/presets", json={"name": "Cheap", "settings": {**defaults, "image_provider": "codex"}}).status_code == 200
    assert [p["name"] for p in client.get("/api/presets").json()] == ["Cheap"]
    client.delete("/api/presets/Cheap")
    assert client.get("/api/presets").json() == []


def test_max_veo_shots_setting(client, project, root, monkeypatch):
    from ui.server.runner import settings_env
    monkeypatch.delenv("MAX_VEO_SHOTS", raising=False)
    settings = client.get(f"/api/projects/{project}/settings").json()["settings"]
    assert settings["max_veo_shots"] == 3
    for value, words in ((-1, "not be negative"), (11, "10 or fewer"), (2.5, "whole number"), ("many", "whole number")):
        r = client.put(f"/api/projects/{project}/settings", json={**settings, "max_veo_shots": value})
        assert r.status_code == 400 and words in r.json()["detail"], r.json()
    saved = client.put(f"/api/projects/{project}/settings", json={**settings, "max_veo_shots": 0}).json()["settings"]
    assert saved["max_veo_shots"] == 0
    assert client.get(f"/api/projects/{project}/settings").json()["settings"]["max_veo_shots"] == 0
    assert settings_env(saved)["MAX_VEO_SHOTS"] == "0"
    # Settings saved before the key existed read as the default.
    old = {k: v for k, v in settings.items() if k != "max_veo_shots"}
    (root / "projects" / project / "ui_settings.json").write_text(json.dumps(old))
    (root / "ui" / "data").mkdir(parents=True, exist_ok=True)
    (root / "ui" / "data" / "defaults.json").write_text(json.dumps(old))
    assert client.get("/api/defaults").json()["max_veo_shots"] == 3
    assert client.get(f"/api/projects/{project}/settings").json()["settings"]["max_veo_shots"] == 3
    assert client.put("/api/defaults", json={**old, "max_veo_shots": 5}).json()["max_veo_shots"] == 5


def test_veo_extend_long_shots_setting(client, project, root, monkeypatch):
    from ui.server.runner import settings_env
    monkeypatch.delenv("VEO_EXTEND_LONG_SHOTS", raising=False)
    settings = client.get(f"/api/projects/{project}/settings").json()["settings"]
    assert settings["veo_extend_long_shots"] is False
    assert settings_env(settings)["VEO_EXTEND_LONG_SHOTS"] == "false"
    for value in ("false", 1):  # strict: the string "false" must not read as on
        r = client.put(f"/api/projects/{project}/settings", json={**settings, "veo_extend_long_shots": value})
        assert r.status_code == 400 and "true or false" in r.json()["detail"], r.json()
    saved = client.put(f"/api/projects/{project}/settings", json={**settings, "veo_extend_long_shots": True}).json()["settings"]
    assert saved["veo_extend_long_shots"] is True
    assert client.get(f"/api/projects/{project}/settings").json()["settings"]["veo_extend_long_shots"] is True
    assert settings_env(saved)["VEO_EXTEND_LONG_SHOTS"] == "true"
    # Settings saved before the key existed read as off.
    old = {k: v for k, v in settings.items() if k != "veo_extend_long_shots"}
    (root / "projects" / project / "ui_settings.json").write_text(json.dumps(old))
    (root / "ui" / "data").mkdir(parents=True, exist_ok=True)
    (root / "ui" / "data" / "defaults.json").write_text(json.dumps(old))
    assert client.get("/api/defaults").json()["veo_extend_long_shots"] is False
    assert client.get(f"/api/projects/{project}/settings").json()["settings"]["veo_extend_long_shots"] is False
    # The environment sets the built-in default; a value that is not a boolean is left to the run to report.
    (root / "ui" / "data" / "defaults.json").unlink()
    monkeypatch.setenv("VEO_EXTEND_LONG_SHOTS", "on")
    assert client.get("/api/defaults").json()["veo_extend_long_shots"] is True
    monkeypatch.setenv("VEO_EXTEND_LONG_SHOTS", "maybe")
    assert client.get("/api/defaults").json()["veo_extend_long_shots"] is False


# --------------------------------------------------------------------- run

def test_run_to_finish_then_reviews_outputs_and_cover(client, project):
    client.post("/api/projects", json={"name": "empty_one"})
    assert client.post("/api/projects/empty_one/run", json={}).status_code == 400  # no screenshots
    assert client.get(f"/api/projects/{project}/cover").json()["enabled"] is False

    r = client.post(f"/api/projects/{project}/run", json={"mode": "resume"})
    assert r.status_code == 200 and r.json()["state"] == "running"
    assert client.post(f"/api/projects/{project}/run", json={}).status_code == 409
    assert client.delete(f"/api/projects/{project}").status_code == 409
    status = wait_for(client, project, {"finished", "stopped"})
    assert status["state"] == "finished", status
    assert status["percent"] == 100 and all(s["state"] == "done" for s in status["steps"])
    assert len(status["shots"]) == 6 and all(s["state"] == "approved" for s in status["shots"])
    assert {s["type"] for s in status["shots"]} == {"still", "video"}
    assert status["shots"][0]["preview_url"].startswith(f"/files/{project}/generated/")
    assert client.get(status["shots"][0]["preview_url"]).status_code == 200
    assert any("Step 3 of 6 — Planning the shots" in line for line in status["log_tail"])

    summary = next(p for p in client.get("/api/projects").json() if p["name"] == project)
    assert summary["status"] == "finished" and summary["spent_usd"] > 0

    log = client.get(f"/api/projects/{project}/log?offset=0").json()
    assert "Finished!" in log["text"]
    assert client.get(f"/api/projects/{project}/log?offset={log['offset']}").json()["text"] == ""

    reviews = client.get(f"/api/projects/{project}/reviews").json()
    assert [s["verdict"] for s in reviews["story"]] == ["REJECT", "APPROVE"]
    assert reviews["story"][0]["issues"] and reviews["final_script"]
    redo = next(s for s in reviews["shots"] if s["number"] == 3)
    assert [a["verdict"] for a in redo["attempts"]] == ["reject", "approve"]
    video = next(s for s in reviews["shots"] if s["type"] == "video")
    assert video["attempts"][0]["camera_motion"]

    outputs = client.get(f"/api/projects/{project}/outputs").json()
    assert outputs["reel_url"] and outputs["audio_url"] and outputs["subtitles"]
    assert len(outputs["stills"]) == 4 and len(outputs["clips"]) == 2
    assert len(outputs["clips"][0]["previews"]) == 3

    # Cover: make one, revise it, restore the first version.
    cover = client.post(f"/api/projects/{project}/cover/run", json={"hook": "Look first"}).json()
    assert cover["status"]["state"] == "running"
    cover = wait_for(client, project, {"finished", "stopped"}, path="cover")
    assert cover["exists"] and cover["hook_text"] == "Look first" and cover["grid_url"]
    client.post(f"/api/projects/{project}/cover/revise", json={"feedback": "bigger headline"})
    cover = wait_for(client, project, {"finished"}, path="cover")
    # v1 is the first cover (kept as cover_v1.png); v2 is the live one, made from the feedback.
    assert [(v["version"], v["feedback"], v["current"]) for v in cover["versions"]] == [
        (1, None, False), (2, "bigger headline", True)]
    restored = client.post(f"/api/projects/{project}/cover/restore", json={"version": 1}).json()
    assert [(v["version"], v["current"]) for v in restored["versions"]] == [(1, False), (2, False), (3, True)]
    # The reel stays finished: the cover run has its own status.
    assert client.get(f"/api/projects/{project}/status").json()["state"] == "finished"


def test_start_over_archives_outputs(client, project, root):
    client.post(f"/api/projects/{project}/run", json={})
    wait_for(client, project, {"finished"})
    client.post(f"/api/projects/{project}/run", json={"mode": "start_over"})
    archive = list((root / "projects" / project / "_archive").iterdir())
    assert len(archive) == 1 and (archive[0] / "book_reel.mp4").is_file()
    assert wait_for(client, project, {"finished"})["percent"] == 100


def test_pause_after_narration_edit_rewrite_continue(client, project):
    r = client.post(f"/api/projects/{project}/run", json={"pause_after_narration": True})
    assert r.json()["pause_after_narration"] is True
    status = wait_for(client, project, {"paused", "finished", "stopped"})
    assert status["state"] == "paused", status
    assert status["steps"][1]["state"] == "waiting_for_you" and status["percent"] == 35

    narration = client.get(f"/api/projects/{project}/narration").json()
    assert narration["word_count"] > 80 and narration["critic"]["verdict"] == "APPROVE"
    assert narration["history"][0]["label"] == "Original (written by Claude)"
    assert narration["downstream_done"] is False
    edited = narration["script"].replace("weirdly specific", "strangely exact")
    after = client.put(f"/api/projects/{project}/narration", json={"script": edited}).json()
    assert after["script"] == edited and [h["label"] for h in after["history"]] == [
        "Original (written by Claude)", "Your edit 1"]
    assert client.put(f"/api/projects/{project}/narration", json={"script": "too short"}).status_code == 400
    back = client.post(f"/api/projects/{project}/narration/discard").json()
    assert back["script"] == narration["script"]

    client.post(f"/api/projects/{project}/narration/rewrite", json={"instructions": "punchier ending"})
    status = wait_for(client, project, {"paused", "stopped", "finished"})
    assert status["state"] == "paused"
    rewritten = client.get(f"/api/projects/{project}/narration").json()
    assert "guessing blind" in rewritten["script"] and rewritten["history"][-1]["label"] == "Rewritten by Claude"

    r = client.post(f"/api/projects/{project}/narration/continue")
    assert r.status_code == 200 and r.json()["pause_after_narration"] is False
    assert wait_for(client, project, {"finished", "stopped"})["state"] == "finished"


def test_pause_toggle_too_late(client, project, monkeypatch):
    monkeypatch.setenv("FAKE_RUN_SECONDS", "20")
    client.post(f"/api/projects/{project}/run", json={})
    deadline = time.time() + 20
    while client.get(f"/api/projects/{project}/status").json()["step"] < 3 and time.time() < deadline:
        time.sleep(0.2)
    r = client.post(f"/api/projects/{project}/pause-after-narration", json={"enabled": True})
    assert r.status_code == 409 and "Too late" in r.json()["detail"]
    client.post(f"/api/projects/{project}/stop")


def test_stop_run(client, project, monkeypatch):
    monkeypatch.setenv("FAKE_RUN_SECONDS", "60")
    client.post(f"/api/projects/{project}/run", json={})
    time.sleep(1.5)
    status = client.post(f"/api/projects/{project}/stop").json()
    assert status["state"] == "stopped" and status["stop_kind"] == "operator"
    assert status["stop_reason"] == "You stopped the run."
    # A resume starts again.
    monkeypatch.setenv("FAKE_RUN_SECONDS", "3")
    assert client.post(f"/api/projects/{project}/run", json={}).json()["state"] == "running"
    assert wait_for(client, project, {"finished", "stopped"})["state"] == "finished"


def test_budget_stop_reason(client, project, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL", "budget")
    client.post(f"/api/projects/{project}/run", json={})
    status = wait_for(client, project, {"stopped", "finished"})
    assert status["state"] == "stopped" and status["stop_kind"] == "budget"
    assert "budget" in status["stop_reason"]


# -------------------------------------------------------------- characters

def test_characters_crud(client, project, root):
    chars = client.get("/api/characters").json()
    assert [c["id"] for c in chars] == ["default"] and chars[0]["is_default"]
    assert (root / "assets" / "character" / "character.png").is_file()  # copied, not moved
    r = client.post("/api/characters", data={"name": "Sachin", "description": "green jacket"},
                    files={"main": ("m.jpg", png((1, 2, 3)), "image/jpeg")})
    assert r.status_code == 200
    sachin = r.json()
    assert sachin["id"] == "sachin" and sachin["main_url"] and sachin["face_url"] is None
    assert client.get(sachin["main_url"]).status_code == 200
    assert client.post("/api/characters", data={"name": "No image"}).status_code == 400
    assert client.post("/api/characters", data={"name": "Bad"},
                       files={"main": ("x.png", b"not an image", "image/png")}).status_code == 400
    updated = client.put("/api/characters/sachin", data={"name": "Sachin P"},
                         files={"face": ("f.png", png(), "image/png")}).json()
    assert updated["name"] == "Sachin P" and updated["face_url"]
    listing = client.post("/api/characters/sachin/default").json()
    assert [c["id"] for c in listing if c["is_default"]] == ["sachin"]
    assert client.get("/api/defaults").json()["character_id"] == "sachin"
    copy = client.post("/api/characters/sachin/duplicate").json()
    assert copy["id"] == "sachin_copy" and not copy["is_default"]
    settings = client.get(f"/api/projects/{project}/settings").json()["settings"]
    client.put(f"/api/projects/{project}/settings", json={**settings, "character_id": "sachin"})
    assert client.get("/api/characters").json()[1]["used_by"] == [project]
    deleted = client.delete("/api/characters/sachin").json()
    assert deleted == {"ok": True, "affected": [project]}
    assert client.get(f"/api/projects/{project}/settings").json()["settings"]["character_id"] is None
    assert any(c["is_default"] for c in client.get("/api/characters").json())


# ------------------------------------------------------------------ styles

STYLE = {"name": "neon_test", "description": "Glowing lines", "palette": ["magenta", "cyan"],
         "lighting": "night", "mood": "electric", "avoid": ["text"]}


def upload_style(client, data, conflict=None, image=None):
    files = {"json": ("s.json", data if isinstance(data, bytes) else json.dumps(data).encode(), "application/json")}
    if image:
        files["image"] = ("s.png", image, "image/png")
    return client.post("/api/styles" + (f"?on_conflict={conflict}" if conflict else ""), files=files)


def test_styles_upload_validation_conflict(client, project):
    assert [s["id"] for s in client.get("/api/styles").json()] == ["flat_editorial", "pixel_art"]
    r = upload_style(client, b"{not json")
    assert r.status_code == 400 and "not valid JSON" in r.json()["detail"]
    r = upload_style(client, {k: v for k, v in STYLE.items() if k != "palette"})
    assert r.status_code == 400 and r.json()["detail"] == "`palette` is missing."
    r = upload_style(client, {**STYLE, "colour": "red"})
    assert r.status_code == 400 and "`colour` is not a style field" in r.json()["detail"]
    r = upload_style(client, STYLE, image=png())
    assert r.status_code == 200 and r.json()["image_url"]
    assert client.get(r.json()["image_url"]).status_code == 200
    r = upload_style(client, STYLE)
    assert r.status_code == 409 and r.json()["conflict"] is True
    both = upload_style(client, STYLE, "keep_both").json()
    assert both["id"] == "neon_test_2"
    replaced = upload_style(client, {**STYLE, "mood": "calm"}, "replace").json()
    assert replaced["id"] == "neon_test" and replaced["mood"] == "calm"
    settings = client.get(f"/api/projects/{project}/settings").json()["settings"]
    client.put(f"/api/projects/{project}/settings", json={**settings, "source_mode": "content", "style": "neon_test"})
    assert client.delete("/api/styles/neon_test").json() == {"ok": True, "affected": [project]}
    assert client.get(f"/api/projects/{project}/settings").json()["settings"]["style"] == "flat_editorial"
    assert client.get("/api/styles/neon_test/image").status_code == 404


# ----------------------------------------------------------------- caption

def test_caption_write_edit_revise_and_restore(client, project, root):
    assert client.get(f"/api/projects/{project}/caption").json()["enabled"] is False
    assert client.post(f"/api/projects/{project}/caption/run", json={}).status_code == 400  # no narration
    (root / "projects" / project / "metadata").mkdir(parents=True, exist_ok=True)
    (root / "projects" / project / "metadata" / "final_story_plan.json").write_text(
        json.dumps({"narration_script": "You have one month."}))

    body = client.post(f"/api/projects/{project}/caption/run", json={}).json()
    assert body["enabled"] and body["status"]["state"] == "running"
    body = wait_for(client, project, {"finished", "stopped"}, path="caption")
    assert body["status"]["state"] == "finished" and body["text"].endswith("#bookreels")
    assert body["problems"] == [] and [(v["version"], v["current"]) for v in body["versions"]] == [(1, True)]

    edited = body["text"].replace("It's your benchmark.", "It's your yardstick.")
    body = client.put(f"/api/projects/{project}/caption", json={"text": edited}).json()
    assert "yardstick" in body["text"]
    assert [(v["version"], v["change"], v["current"]) for v in body["versions"]] == [
        (1, None, False), (2, "edit", True)]
    assert client.put(f"/api/projects/{project}/caption", json={"text": " "}).status_code == 400
    # A hand edit that breaks a limit is saved, and its problems are listed.
    body = client.put(f"/api/projects/{project}/caption", json={"text": "No tags at all"}).json()
    assert any("0 hashtags" in p for p in body["problems"])

    client.post(f"/api/projects/{project}/caption/revise", json={"feedback": "friendlier"})
    body = wait_for(client, project, {"finished"}, path="caption")
    assert "revised: friendlier" in body["text"]
    assert [(v["change"], v["feedback"]) for v in body["versions"]][-1] == ("feedback", "friendlier")

    body = client.post(f"/api/projects/{project}/caption/restore", json={"version": 2}).json()
    assert "yardstick" in body["text"] and body["versions"][-1]["change"] == "restore"
    assert client.post(f"/api/projects/{project}/caption/restore", json={"version": 99}).status_code == 404
    # The reel's own status is untouched by caption runs.
    assert client.get(f"/api/projects/{project}/status").json()["state"] == "idle"


# ------------------------------------------------------------------- files

def test_files_refuse_paths_outside_the_project(client, project, root):
    (root / "secret.txt").write_text("nope")
    assert client.get(f"/files/{project}/%2E%2E/%2E%2E/secret.txt").status_code == 403
    assert client.get(f"/files/{project}/..%2F..%2Fsecret.txt").status_code == 403
    assert client.get(f"/files/{project}/ui_settings.json").status_code == 200
    assert client.get("/files/bad%20name/x.png").status_code == 400


def test_setup_env_roundtrip(client, root):
    (root / ".env").write_text("# keep me\nLANGFUSE_SECRET_KEY=sk-lf-1234567890abcdef\nGOOGLE_CLOUD_PROJECT=old\n")
    body = client.put("/api/setup", json={"project_id": "new-proj", "location": "us-central1",
                                          "gcs_bucket_uri": "gs://b/x/"}).json()
    assert body["project_id"] == "new-proj" and body["location"] == "us-central1"
    text = (root / ".env").read_text()
    assert "# keep me" in text and "GOOGLE_CLOUD_PROJECT=new-proj" in text and "GCS_BUCKET_URI=gs://b/x/" in text
    key = next(k for k in body["keys"] if k["name"] == "LANGFUSE_SECRET_KEY")
    assert key["set"] and "1234567890" not in key["masked"]
    assert client.put("/api/setup", json={"gcs_bucket_uri": "http://nope"}).status_code == 400


# ------------------------------------------------------------------ voices

def test_voice_sample_is_made_once_then_reused(client, root, monkeypatch):
    from ui.server import store
    calls = []

    def fake_tts(voice, path):
        calls.append(voice)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF-" + voice.encode())

    monkeypatch.setattr(store, "synthesize_sample", fake_tts)
    for _ in range(2):
        r = client.get("/api/voices/Puck/sample")
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
        assert r.content == b"RIFF-Puck"
    assert calls == ["Puck"]
    assert (root / "assets" / "voice_samples" / "Puck.wav").is_file()
    assert client.get("/api/voices/NotAVoice/sample").status_code == 404


def test_voice_sample_tts_failure_leaves_no_file(client, root, monkeypatch):
    from ui.server import store

    def broken_tts(voice, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"half")
        raise RuntimeError("503 Service Unavailable")

    monkeypatch.setattr(store, "synthesize_sample", broken_tts)
    r = client.get("/api/voices/Kore/sample")
    assert r.status_code == 502 and "503 Service Unavailable" in r.json()["detail"]
    assert not any((root / "assets" / "voice_samples").iterdir())
