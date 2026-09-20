# test/ — Gemini image playground

Scratch space for poking at the single image-edit call the pipeline makes.
Nothing here is imported by the pipeline, run by pytest, or written anywhere
outside `test/out/`.

The real tests live in `orchestrator/tests/`.

## Why

When a shot is refused (`block_reason=OTHER`) or comes out wrong, the pipeline
is a slow way to find out why — it costs a whole agent session per attempt.
This runs the same call directly so you can change one thing and look.

## Use

```bash
conda activate kayak-video

python test/try_seed.py --list           # what shots exist, and which have people
python test/try_seed.py --show-prompt    # the exact prompt the pipeline sends (free, no API call)
python test/try_seed.py                  # run the CONFIG block
python test/try_seed.py --shot 5         # try a different shot
python test/try_seed.py --sweep          # run every EXPERIMENT and summarise
```

## The loop

1. Open `try_seed.py`, edit the **CONFIG** block at the top:

   | Setting | What it does |
   |---|---|
   | `SHOT` | which shot from `metadata/visual_plan.json` |
   | `MODE` | `SEED` for video shots, `STILL` for stills |
   | `MODEL` | the image model to call |
   | `SEND_CHARACTER` | send `assets/character/character.png` |
   | `SEND_FACE` | also send the face crop (blocks more often) |
   | `PROMPT` | `None` = the pipeline's own prompt; a string = yours, verbatim |

2. Run it.
3. Look in `test/out/`.

To start from the real prompt and edit it:

```bash
python test/try_seed.py --show-prompt > /tmp/p.txt
```

then paste it into `PROMPT = """..."""`.

## Output

Each run writes two files to `test/out/`:

```
shot06_manual_143052.png    the image (only if one came back)
shot06_manual_143052.txt    the exact prompt, model, references, block reason
```

The `.txt` is always written — including on a refusal, which is when you most
want to know what was sent. An image can always be traced back to its prompt.

## Reading a refusal

```
RESULT: NO IMAGE   blocked=BlockedReason.OTHER
```

A refusal is deterministic — the same request will be refused again. Change
something rather than retrying. The variables worth changing, roughly in order
of how often they matter:

1. `SEND_CHARACTER` — a real-person likeness is the most common trigger,
   especially in a scene that already contains several people.
2. `SEND_FACE` — a tight photoreal face crop trips the filter hardest.
3. `MODEL` — different models draw the line in different places.
4. `PROMPT` — wording is usually the *least* likely cause; the agent already
   tried stripping named entities and lighting language on shot 6 and was
   refused identically all three times.

## Cost

One run ≈ $0.04. A sweep of three ≈ $0.12. It bills your Google Cloud project,
not the Claude account.
