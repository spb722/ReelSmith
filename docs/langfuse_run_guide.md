# Reading a run in Langfuse

How to find a book_reels run in Langfuse and follow it step by step: what went in and what came out at every stage, every shot and every image call.

## Before you run

1. **Keys in `.env`.** The repo-root `.env` (git-ignored) holds:
   ```
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_BASE_URL=https://cloud.langfuse.com   # or https://us.cloud.langfuse.com
   ```
   It is read at the start of every `orchestrator.run` and `orchestrator.cover` command. A variable already set in your shell wins over `.env`. Write one `KEY=value` per line, with no comment on the same line.

2. **Run with the `kayak-video` Python.** Plain `python` in the `(base)` conda env has older packages (langfuse 3.15.0, claude-agent-sdk 0.2.147) that the pipeline was not built for. Tracing then switches itself off with:
   ```
   Tracing problem, the run carries on without it: starting Langfuse:
   TypeError: Langfuse.__init__() got an unexpected keyword argument 'span_exporter'
   ```
   Use either:
   ```bash
   conda activate kayak-video
   python -m orchestrator.run --project <name>
   ```
   or, without activating:
   ```bash
   /opt/homebrew/anaconda3/envs/kayak-video/bin/python -m orchestrator.run --project <name>
   ```

3. **Check the first lines of the run.** When tracing is on, you see:
   ```
   Tracing this run in Langfuse (session <name>:run_20260925T120007)
   ```
   If that line is missing, or a `Tracing problem` line appears instead, the run is not being traced. The run itself still carries on either way: tracing never stops or slows a run.

## How runs are organised

You never enter or run a project ID. The keys decide which Langfuse project receives the data, and every run of every book project goes there.

```
Langfuse project (chosen by the keys in .env)
 └── Trace = one run of the pipeline, always named "book_reels_run"
      ├── Tag     = the book project name, e.g. 37rule
      └── Session = <project>:<run name>, e.g. 37rule:run_20260925T120007
```

The run name is the log file name the run prints at the start:

```
A copy of everything printed here is being saved to orchestrator_runs/run_20260925T120007.log
                                                                       └─ run name ──┘
```

A resumed run is a new trace with its own session, because it has its own log file.

## Finding a run

1. Open your project in Langfuse and go to **Tracing → Traces**. Each row is one run, newest first.
2. Narrow it down:
   - **Tags = `<project>`** shows every run of one book.
   - **Sessions**, then the session named `<project>:<run name>`, finds one exact run.
   - **Level = ERROR** shows runs that stopped with a failure.
3. Click a trace to open it.

## Reading a trace

The left side is a tree of steps; click any step and the right side shows its **Input**, **Output** and **Metadata**. This tree is from a real `37rule` run that retried shot 5:

```
book_reels_run                    ← the whole run. Output/metadata: why it stopped
│                                   (reason_code, resumable, failed_shots)
├─ preflight                      ← startup checks and their result
├─ asset_analyst                  ← Step 1: reading the screenshots
├─ story_agent                    ← Step 2: the narration
│  └─ story_critic                ← the independent critic's verdict on each draft
├─ visual_agent                   ← Step 3: the shot plan
├─ voice                          ← Step 4
│  ├─ tts                         ← text, voice and model sent to text-to-speech
│  ├─ stt                         ← what was heard back, and the match ratio
│  └─ subtitle_cues               ← how many subtitles were made
├─ production                     ← Step 5: all shots
│  └─ shot_05                     ← one shot
│     └─ stills_agent.query       ← ONE TRY. Input: the prompt we sent Claude.
│        │                          Metadata: system prompt, skills, model, budget cap
│        └─ ClaudeAgentSDK.query  ← Claude's actual turns, tokens and cost
│           ├─ Skill (×3)         ← Claude loading its skills
│           ├─ mcp__gemini__generate_still   ← Claude calling the image tool (its arguments)
│           │  └─ generate_still
│           │     └─ image_edit   ← the EXACT prompt sent to the image model,
│           │                       the likeness-ladder rung, the provider, the cost
│           └─ StructuredOutput   ← Claude's final answer for this try
└─ delivery                       ← Step 6
   └─ render_remotion             ← the render command and the output path
```

A Veo shot has `veo_agent.query` for each try instead. Inside it, `generate_veo_seed` covers the opening image and `generate_veo_clip` covers the video, with `veo.generate_videos` holding the video prompt, duration, 9:16 aspect ratio and GCS URI. After the producer approves a shot, the independent visual reviewer's query sits under the same shot.

### Following one try

Read one try from top to bottom:

1. **`stills_agent.query` or `veo_agent.query`: Input** is what we asked Claude to do: the shot, the subtitle text, the settings, and the previous try's failure if this is a retry.
2. **`image_edit`: Input** is what was actually sent to the image model, after the likeness-ladder rung was applied.
3. **`StructuredOutput`** is what Claude decided: approved, or failed with a `stage`, a `code` and a `reason`.
4. **The reviewer's query**, if there is one, is the second opinion. Its rejection is recorded like any other failure.

Several tries of one shot appear as several `…agent.query` blocks under the same `shot_NN`, in order.

### Stages that show only an output

On a resumed run, stages finished in an earlier run are skipped. They appear with only an output and no Claude steps inside. Open the earlier run's trace to see them.

## What you will not see, on purpose

- **Images** appear as file paths, such as `generated/stills/shot_05.png`, never as pictures. Open the file in the project folder to look at it.
- **Secrets** (API keys, Google tokens, the Langfuse keys) are removed before anything is sent.
- **Long texts** are cut at `LANGFUSE_MAX_CHARS` characters, 20000 by default. Set it higher in `.env` if a prompt looks truncated.

## Common things to look for

| You want to know | Where to look |
| --- | --- |
| Why the run stopped | `book_reels_run` → Output/Metadata: `reason_code` (`vendor_limit`, `budget_exhausted`, `retry_ceiling`, …) and `failed_shots` |
| Why one shot failed | `shot_NN` → the last `…agent.query` → `StructuredOutput` (`stage`, `code`, `reason`) |
| What prompt the image model really got | `image_edit` → Input |
| Whether a shot switched to Codex | `image_edit` → the provider in its input or metadata. After an automatic fallback, the later tries say `codex` |
| What a stage cost | the Claude query's cost, plus each paid tool's `cost_usd` |
| What the story critic said | `story_agent` → `story_critic` → Output |

The same facts are on disk: `projects/<name>/orchestrator_runs/failure_*.json` is the stop report, and `run_<timestamp>.log` is the full console log.
