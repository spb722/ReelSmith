"""A pretend pipeline run for tests and demos. It never calls a vendor.

    python -m ui.server.fake_runner --project NAME [--style S] [--retry-voice]
    python -m ui.server.fake_runner --cover --project NAME [--hook T] [--shot N] [--force] [--feedback T]

Writes the same files the real pipeline does (run_<ts>.log in the terminal's
wording, status.json, story/shot attempt files, metadata, stills, a reel).
Env: FAKE_RUN_SECONDS (default 30) for the whole reel run; FAKE_FAIL =
attempts | budget | vendor to stop with that reason (once; a resume succeeds);
REELSMITH_ROOT for the projects folder, like the server.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("REELSMITH_ROOT") or REPO)
SAMPLES = sorted((REPO / "ui_design" / "extracted").glob("*_shot*.jpg"))
STEPS = [
    ("Looking at your screenshots", 9, "asset_analyst", 0.32),
    ("Writing the narration", 26, "story_agent", 1.18),
    ("Planning the shots", 10, "visual_agent", 0.64),
    ("Recording the voice and building subtitles", 4, "voice_agent", 0.21),
    ("Making the video clips and still pictures", 49, None, 0.0),
    ("Putting the final video together", 2, None, 0.0),
]
SHOT_TYPES = ["still", "video", "still", "still", "video", "still"]
REDO_SHOT = 3
SCRIPT = (
    "You have one month to find an apartment. You step into an empty room and face an ultimatum: sign now, "
    "or walk away forever. But you can't judge a place until you've seen enough to know what good looks like. "
    "And every place you tour is another one that gets snapped up. So how long do you look before you commit? "
    "The answer is a weirdly specific number: thirty-seven percent. For the first thirty-seven percent, you only "
    "look. Commit to nothing. After that, take the first option better than anything you've seen. Looking isn't "
    "wasted time. It's your benchmark. Without it, you're just guessing."
)


def iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class Run:
    def __init__(self, project: Path, kind: str):
        self.project = project
        self.runs = project / "orchestrator_runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        self.start = time.monotonic()
        self.log_path = self.runs / f"run_{datetime.now().strftime('%Y%m%dT%H%M%S')}.log"
        while self.log_path.exists():  # two runs in the same second
            time.sleep(0.2)
            self.log_path = self.runs / f"run_{datetime.now().strftime('%Y%m%dT%H%M%S')}.log"
        self.log_file = self.log_path.open("a", encoding="utf-8")
        self.kind = kind

    def log(self, message: str, indent: int = 1) -> None:
        total = int(time.monotonic() - self.start)
        line = f"[{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}] {'  ' * indent}{message}"
        print(line, flush=True)
        self.log_file.write(line + "\n")
        self.log_file.flush()

    def banner(self, message: str) -> None:
        self.log(f"▸ {message}", indent=0)


def sample_image(n: int) -> Path | None:
    return SAMPLES[(n - 1) % len(SAMPLES)] if SAMPLES else None


def save_png(src: Path | None, dst: Path, size=(1080, 1920)) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        if src is None:
            Image.new("RGB", size, (230, 220, 200)).save(dst)
            return
        with Image.open(src) as image:
            image.convert("RGB").resize(size).save(dst, format="PNG" if dst.suffix == ".png" else "JPEG")
    except ImportError:
        if src is not None:
            shutil.copyfile(src, dst)


def tiny_mp4() -> Path | None:
    clips = [p for p in (REPO / "projects").glob("*/generated/veo/*.mp4") if p.stat().st_size > 10_000]
    return min(clips, key=lambda p: p.stat().st_size) if clips else None


def silent_wav(path: Path, seconds: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000)
        w.writeframes(b"\x00\x00" * int(8000 * seconds))


# ------------------------------------------------------------------ reel

def reel(args) -> int:
    project = ROOT / "projects" / args.project
    if not project.is_dir():
        print(f"STOPPED — No project called {args.project!r}", file=sys.stderr)
        return 1
    run = Run(project, "reel")
    total = float(os.environ.get("FAKE_RUN_SECONDS", "30"))
    fail = os.environ.get("FAKE_FAIL", "").strip().lower()
    rewrite = os.environ.get("STORY_REWRITE_INSTRUCTIONS", "").strip()
    budget = float(os.environ.get("MAX_BUDGET_USD", "40"))
    config_path = run.runs / "config.json"
    config = read_json(config_path, {}) or {}
    limits = {"asset_analyst": 4, "story_agent": 4, "visual_agent": 4, "voice_agent": 3, "veo_agent": 3,
              "stills_agent": 3, **(config.get("limits", {}).get("max_attempts") or {})}
    usage = config.setdefault("usage", {})
    usage.setdefault("attempts_used", {})
    usage.setdefault("budget_spent_usd", 0.0)
    previous = read_json(run.runs / "status.json", {}) or {}
    if previous.get("state") == "stopped" and previous.get("stop_kind") == fail:
        fail = ""  # it already failed this way once; the resume goes through

    old_steps = previous.get("steps") or []
    steps = [{"index": i + 1, "name": name, "state": "waiting", "attempt": 0,
              "max_attempts": limits.get(key, 1) if key else (limits["stills_agent"] if i == 4 else 1),
              "started_at": None, "finished_at": None, "cost_usd": 0.0}
             for i, (name, _w, key, _c) in enumerate(STEPS)]
    for old, new in zip(old_steps, steps):
        if old.get("state") in ("done", "waiting_for_you"):
            new.update({k: old.get(k, new[k]) for k in ("attempt", "started_at", "finished_at", "cost_usd")})
            new["state"] = "done"
    if rewrite:
        steps[1]["state"] = "waiting"
    shots = previous.get("shots") if previous.get("shots") else []
    state = {"kind": "reel", "state": "running", "percent": 0, "step": 0, "steps": steps, "shots": shots,
             "spent_usd": round(usage["budget_spent_usd"], 2), "budget_usd": budget, "started_at": iso(),
             "updated_at": iso(), "stop_reason": None, "stop_kind": None}

    def save(percent=None):
        if percent is not None:
            state["percent"] = max(state["percent"], int(round(percent)))
        state["spent_usd"] = round(usage["budget_spent_usd"], 2)
        state["updated_at"] = iso()
        write_json(run.runs / "status.json", state)
        write_json(config_path, config)

    def spend(amount, step_index=None):
        usage["budget_spent_usd"] = round(usage["budget_spent_usd"] + amount, 4)
        if step_index is not None:
            steps[step_index]["cost_usd"] = round(steps[step_index]["cost_usd"] + amount, 2)

    def halt(reason, kind, stage):
        state.update({"state": "stopped", "stop_reason": reason, "stop_kind": kind})
        for s in steps:
            if s["state"] in ("running", "retrying"):
                s["state"] = "failed"
        write_json(run.runs / f"failure_{stage}_{datetime.now().strftime('%Y-%m-%dT%H%M%S%f')}.json",
                   {"stage_id": stage, "reason": reason, "attempt_count": 1, "timestamp": iso()})
        save()
        run.log(f"STOPPED — {reason}")
        return 1

    def on_term(signum, frame):
        state.update({"state": "stopped", "stop_reason": "You stopped the run.", "stop_kind": "operator"})
        for s in steps:
            if s["state"] in ("running", "retrying"):
                s["state"] = "waiting"
        save()
        run.log("STOPPED — stopped by the operator")
        sys.exit(143)

    signal.signal(signal.SIGTERM, on_term)
    run.banner(f"Starting a reel in project '{args.project}'")
    run.log(f"Everything this run writes stays in {project}")
    run.log(f"A copy of everything printed here is being saved to orchestrator_runs/{run.log_path.name}")
    run.log("(fake run: no vendor is called and nothing is spent)")
    if os.environ.get("SOURCE_MODE") == "content":
        run.log(f"Art style: {args.style or 'flat_editorial'} (styles/{args.style or 'flat_editorial'}.json)")
    run.log("Startup checks passed · reading screenshots from source_images/")
    save()

    done_pct = 0
    for i, (name, weight, key, cost) in enumerate(STEPS):
        step = steps[i]
        if step["state"] == "done" and not (i == 3 and args.retry_voice):
            done_pct += weight
            continue
        state["step"] = i + 1
        step.update({"state": "running", "attempt": 1, "started_at": iso(), "finished_at": None})
        run.banner(f"Step {i + 1} of 6 — {name} · ~{done_pct}%")
        save(done_pct)
        seconds = max(0.3, total * weight / 100)
        if i == 3 and fail == "vendor":
            time.sleep(seconds / 2)
            return halt("The voice service (Gemini TTS) returned an error: 503 Service Unavailable. "
                        "Try again in a few minutes.", "vendor", "voice_agent")
        if i == 4:
            code = make_shots(run, state, steps, limits, usage, seconds, done_pct, weight, fail, budget,
                              spend, save, halt)
            if code is not None:
                return code
        else:
            run.log(f"Try 1 of {step['max_attempts']} · ${budget - usage['budget_spent_usd']:.2f} left to spend")
            if i == 1:
                time.sleep(seconds * 0.5)
                write_story(run, 1, rewrite, approve=False)
                spend(cost / 2, i)
                usage["attempts_used"]["story_agent"] = 1
                run.log("Try 1 of %d didn't work — the critic asked for a sharper ending" % step["max_attempts"])
                step.update({"state": "retrying", "attempt": 2})
                save(done_pct + weight * 0.5)
                time.sleep(seconds * 0.5)
                write_story(run, 2, rewrite, approve=True)
                spend(cost / 2, i)
                usage["attempts_used"]["story_agent"] = 2
            else:
                time.sleep(seconds)
                spend(cost, i)
                if key:
                    usage["attempts_used"][key] = usage["attempts_used"].get(key, 0) + 1
                write_step_outputs(run, i)
            run.log(f"Done in {seconds:.0f}s · cost ${cost:.2f}")
        done_pct += weight
        step.update({"state": "done", "finished_at": iso()})
        run.log(f"Step {i + 1} took {seconds:.0f}s · ${usage['budget_spent_usd']:.2f} spent so far · ~{done_pct}% done")
        save(done_pct)
        if i == 1 and (run.runs / "pause_after_narration.flag").exists():
            step["state"] = "waiting_for_you"
            state["state"] = "paused_for_narration"
            save(done_pct)
            run.log("Paused after the narration, as asked. Read it in the studio, then continue.")
            return 0

    reel_src = tiny_mp4()
    if reel_src:
        shutil.copyfile(reel_src, project / "book_reel.mp4")
    else:
        (project / "book_reel.mp4").write_bytes(b"")
    state.update({"state": "finished", "step": 6})
    save(100)
    run.log(f"Finished! Your video is at {project / 'book_reel.mp4'}")
    return 0


# A real, contract-valid story plan, so the studio's narration editor (which
# validates edits against the plan contract) works on fake runs too.
TEMPLATE_PLAN = REPO / "projects" / "37rule" / "metadata" / "final_story_plan.json"


def write_story(run: Run, attempt: int, rewrite: str, approve: bool) -> None:
    plan = read_json(TEMPLATE_PLAN) or {
        "produced_by": "story_agent", "story_title": "The 37 Percent Rule", "hook": SCRIPT.split(". ")[0] + ".",
        "narration_script": SCRIPT, "scenes": [{"sequence": 1, "narration": SCRIPT}],
        "story_arc": {"principle_explanation": "Look first, then leap.",
                      "viewer_reflection": "Looking is not wasted time; it is your benchmark."}}
    if rewrite:  # a visible change, in the script and in its scene, so the plan stays consistent
        plan = json.loads(json.dumps(plan).replace("you're just guessing", "you're guessing blind"))
    script = plan["narration_script"]
    issues = [] if approve else [{"dimension": "hook_strength", "evidence": "The ending trails off.",
                                  "fix": "End on the benchmark idea in one short line."}]
    write_json(run.runs / f"story_agent_attempt_{attempt}.json",
               {"structured_output": {**plan, "narration_script": script}, "claude_cost_usd": 0.5})
    write_json(run.runs / f"story_agent_attempt_{attempt}_review.json",
               {"structured_output": {"produced_by": "story_critic", "verdict": "APPROVE" if approve else "REJECT",
                                      "issues": issues}, "approved": approve, "claude_cost_usd": 0.1})
    if approve:
        write_json(run.project / "metadata" / "final_story_plan.json", plan)


def write_step_outputs(run: Run, index: int) -> None:
    meta = run.project / "metadata"
    if index == 0:
        write_json(meta / "analyzed_assets.json", {"produced_by": "asset_analyst", "assets": []})
    elif index == 2:
        write_json(meta / "visual_plan.json", {"produced_by": "visual_agent", "shots": [
            {"sequence": n, "generation_mode": "VEO" if t == "video" else "STILL",
             "frame_composition": f"Shot {n}: a calm flat illustration for this line of the narration."}
            for n, t in enumerate(SHOT_TYPES, start=1)]})
    elif index == 3:
        silent_wav(run.project / "audio" / "narration.wav")
        plan = read_json(meta / "final_story_plan.json", {}) or {}
        words = (plan.get("narration_script") or SCRIPT).split()
        cues = [{"cue_id": f"cue_{i // 4 + 1:03d}", "text": " ".join(words[i:i + 4]),
                 "start_seconds": round(i * 0.4, 2), "end_seconds": round((i + 4) * 0.4, 2)}
                for i in range(0, len(words), 4)]
        write_json(meta / "subtitle_cues.json", {"produced_by": "voice_agent", "cues": cues})


def make_shots(run, state, steps, limits, usage, seconds, done_pct, weight, fail, budget, spend, save, halt):
    """Step 5: shots made three at a time; one gets a redo; FAKE_FAIL may stop it."""
    if fail == "budget":
        time.sleep(seconds * 0.1)
        return halt(f"The next shot would go over the ${budget:.0f} budget.", "budget", "stills_agent")
    shots = {s["number"]: s for s in state["shots"]}
    for n, kind in enumerate(SHOT_TYPES, start=1):
        if n not in shots or shots[n].get("state") != "approved":
            shots[n] = {"number": n, "type": kind, "state": "queued", "attempt": 0,
                        "max_attempts": limits["veo_agent" if kind == "video" else "stills_agent"],
                        "preview": None, "prompt": f"Shot {n}: a calm flat illustration for this line of the narration."}
    state["shots"] = [shots[n] for n in sorted(shots)]
    todo = [s for s in state["shots"] if s["state"] != "approved"]
    run.log(f"Making {len(todo)} shot(s), up to 3 at a time")
    phase = seconds / max(1, (len(todo) + 2) // 3) / 3  # making, reviewing, (maybe) redo per group
    finished = len(state["shots"]) - len(todo)
    for g in range(0, len(todo), 3):
        group = todo[g:g + 3]
        for shot in group:
            shot.update({"state": "making", "attempt": shot["attempt"] + 1})
            run.log(f"Shot {shot['number']}: making a {'video clip' if shot['type'] == 'video' else 'still picture'}")
        save()
        time.sleep(phase)
        for shot in group:
            write_media(run, shot)
            shot["state"] = "reviewing"
            run.log(f"Shot {shot['number']}: having the result checked by an independent reviewer", indent=2)
        save()
        time.sleep(phase)
        redo = []
        for shot in group:
            n, stage = shot["number"], "veo_agent" if shot["type"] == "video" else "stills_agent"
            key = f"{stage}_shot_{n}"
            usage["attempts_used"][key] = shot["attempt"]
            spend(1.24 if shot["type"] == "video" else 0.44, 4)
            if fail == "attempts" and n == 4:
                shot["state"] = "failed"
                write_attempt(run, shot, False, "Letters appeared on the journal page; the style forbids any text.")
                return halt(f"Shot {n} used all {shot['max_attempts']} attempts. Raise the still-picture attempt "
                            "limit or resume.", "attempts", stage)
            if n == REDO_SHOT and shot["attempt"] == 1:
                shot["state"] = "redo"
                write_attempt(run, shot, False, "The character's glasses are missing. Match the reference.")
                run.log(f"Shot {n}: try 1 of {shot['max_attempts']} didn't work — the character's glasses are missing")
                redo.append(shot)
                continue
            shot["state"] = "approved"
            write_attempt(run, shot, True, "Matches the shot plan. No text or artefacts.")
            finished += 1
            pct = done_pct + weight * finished / len(state["shots"])
            run.log(f"Shot {n}: {'clip' if shot['type'] == 'video' else 'picture'} approved → {shot['preview']} · "
                    f"{finished} of {len(state['shots'])} shots · ~{pct:.0f}%", indent=2)
            save(pct)
        save()
        for shot in redo:
            steps[4]["state"] = "retrying"
            shot.update({"state": "making", "attempt": 2})
            save()
            time.sleep(phase)
            write_media(run, shot)
            usage["attempts_used"][f"stills_agent_shot_{shot['number']}"] = 2
            spend(0.44, 4)
            shot["state"] = "approved"
            write_attempt(run, shot, True, "Glasses and green jacket now match the reference.")
            finished += 1
            steps[4]["state"] = "running"
            save(done_pct + weight * finished / len(state["shots"]))
    return None


def write_media(run: Run, shot: dict) -> None:
    n, project = shot["number"], run.project
    src = sample_image(n + shot["attempt"])
    if shot["type"] == "video":
        save_png(src, project / "generated" / "veo_seeds" / f"shot_{n:02d}_seed.png")
        for k in (1, 2, 3):
            save_png(sample_image(n + k), project / "generated" / "veo" / "previews" / f"shot_{n:02d}_{k:02d}.jpg",
                     (360, 640))
        clip = tiny_mp4()
        if clip:
            shutil.copyfile(clip, project / "generated" / "veo" / f"shot_{n:02d}.mp4")
        shot["preview"] = f"generated/veo/previews/shot_{n:02d}_01.jpg"
    else:
        save_png(src, project / "generated" / "stills" / f"shot_{n:02d}.png")
        shot["preview"] = f"generated/stills/shot_{n:02d}.png"


def write_attempt(run: Run, shot: dict, ok: bool, comment: str) -> None:
    n, video = shot["number"], shot["type"] == "video"
    stage = "veo_agent" if video else "stills_agent"
    result = {"local_video_path": f"generated/veo/shot_{n:02d}.mp4",
              "preview_paths": [f"generated/veo/previews/shot_{n:02d}_0{k}.jpg" for k in (1, 2, 3)],
              "camera_motion": "Planned: slow push in · Seen: slow push in"} if video else \
        {"local_image_path": f"generated/stills/shot_{n:02d}.png"}
    write_json(run.runs / f"{stage}_shot_{n}_attempt_{shot['attempt']}.json", {"structured_output": {
        "produced_by": stage, "status": "SUCCESS" if ok else "FAILURE", "shot_sequence": n,
        "generation_mode": "VEO" if video else "STILL",
        "result": {**result, "approved": True, "qa_summary": comment} if ok else None,
        "failure": None if ok else {"stage": "still_qa", "reason": comment, "attempt": shot["attempt"],
                                    "partial_artifact_paths": [shot.get("preview")]},
    }})


# ----------------------------------------------------------------- cover

def cover(args) -> int:
    project = ROOT / "projects" / args.project
    if not project.is_dir():
        print(f"STOPPED — No project called {args.project!r}", file=sys.stderr)
        return 1
    run = Run(project, "cover")
    total = float(os.environ.get("FAKE_RUN_SECONDS", "30")) / 4
    status_path = run.runs / "cover_status.json"
    state = {"kind": "cover", "state": "running", "percent": 0, "stop_reason": None, "started_at": iso()}

    def save(percent):
        state.update({"percent": percent, "updated_at": iso()})
        write_json(status_path, state)

    def on_term(signum, frame):
        state.update({"state": "stopped", "stop_reason": "You stopped the run.", "stop_kind": "operator"})
        save(state["percent"])
        sys.exit(143)

    signal.signal(signal.SIGTERM, on_term)
    cover_dir = project / "generated" / "cover"
    cover_dir.mkdir(parents=True, exist_ok=True)
    run.banner(f"Making the Instagram cover for project '{args.project}'")
    run.log("(fake run: no vendor is called and nothing is spent)")
    save(5)
    plan_path = project / "metadata" / "cover_plan.json"
    plan = read_json(plan_path, {}) or {}
    stills = sorted((project / "generated" / "stills").glob("shot_*.png"))
    shot = args.shot or plan.get("base_shot_sequence") or (int(stills[0].stem.split("_")[1]) if stills else 1)
    hook = args.hook or plan.get("hook_text") or "How long do you look before you commit?"
    current = cover_dir / "cover.png"
    if args.feedback and current.is_file():
        version = 1 + max([int(p.stem.split("_v")[1]) for p in cover_dir.glob("cover_v*.png")] or [0])
        kept = cover_dir / f"cover_v{version}.png"
        shutil.copyfile(current, kept)
        run.log(f"Kept the current cover as generated/cover/{kept.name}")
        history = read_json(cover_dir / "feedback_history.json", []) or []
        history.append({"feedback": args.feedback, "previous_cover": f"generated/cover/{kept.name}",
                        "hook_text": plan.get("hook_text", hook), "base_shot_sequence": plan.get("base_shot_sequence", shot)})
        write_json(cover_dir / "feedback_history.json", history)
        run.log(f'Revising from the current cover: "{args.feedback}"')
    elif current.is_file() and not args.force and not args.hook and not args.shot:
        run.log("A cover already exists; use --force to make a fresh one")
    run.log("Cover: drawing it (try 1 of 2)", indent=2)
    time.sleep(total / 2)
    save(50)
    time.sleep(total / 2)
    base = project / "generated" / "stills" / f"shot_{int(shot):02d}.png"
    save_png(base if base.is_file() else sample_image(int(shot) + len(args.feedback)), current)
    try:
        from PIL import Image
        with Image.open(current) as image:
            top = (image.height - image.width * 5 // 4) // 2
            image.crop((0, top, image.width, top + image.width * 5 // 4)).save(cover_dir / "cover_grid.png")
    except ImportError:
        shutil.copyfile(current, cover_dir / "cover_grid.png")
    write_json(plan_path, {"produced_by": "cover_agent", "base_shot_sequence": int(shot), "hook_text": hook,
                           "art_direction": "fake", "rationale": "fake"})
    run.log("Cover written to generated/cover/cover.png (1080x1920)", indent=2)
    run.log("Grid preview written to generated/cover/cover_grid.png (1080x1350)", indent=2)
    state["state"] = "finished"
    save(100)
    run.log("Finished! Your cover is at generated/cover/cover.png")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ui.server.fake_runner")
    parser.add_argument("--project", required=True)
    parser.add_argument("--style", default=None)
    parser.add_argument("--retry-voice", action="store_true")
    parser.add_argument("--cover", action="store_true")
    parser.add_argument("--hook", default="")
    parser.add_argument("--shot", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--feedback", default="")
    args = parser.parse_args(argv)
    return cover(args) if args.cover else reel(args)


if __name__ == "__main__":
    sys.exit(main())
