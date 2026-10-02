"""Ask Gemini whether a script/transcript difference is one phrase written two ways.

Speech-to-text writes speech the way it would be printed: "$20,000",
"Dr.", "1st", "5:00". `align_words` knows only plain numbers and
percentages, so every other printed form counts as missing words and fails
the run even though the voice said them. Rules for each form would never
be finished, so a leftover difference is put to Gemini instead.

Only short spans with matched words on both sides get here (see
`referee_spoken_forms` in deterministic_tools): their timing is already
measured, so the verdict only decides whether the words count as spoken.
"""

from __future__ import annotations

from google import genai
from google.genai import types
from pydantic import BaseModel

from orchestrator.settings import load_settings


class _Verdict(BaseModel):
    same_speech: bool
    reason: str


PROMPT = """A text-to-speech voice read the SCRIPT words aloud. A speech recognizer
then wrote down what it heard as the HEARD words.

SCRIPT: {script}
HEARD: {heard}

Answer same_speech = true only if reading HEARD aloud in the normal way gives
exactly the SCRIPT words, so the only difference is how they are written
(digits, currency, symbols, abbreviations, ordinals, times, dates,
punctuation, spelling variants).

Answer same_speech = false if any word is missing, added, or different in
meaning, even slightly. Example: SCRIPT "twenty dollars" and HEARD "$20,000"
is false.

Give a one-sentence reason."""


def judge_spoken_forms(script_words: list[str], heard_words: list[str]) -> dict:
    settings = load_settings()
    client = genai.Client(vertexai=True, project=settings.project_id, location=settings.location)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=PROMPT.format(script=" ".join(script_words), heard=" ".join(heard_words)),
        config=types.GenerateContentConfig(
            temperature=0, response_mime_type="application/json", response_schema=_Verdict,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    verdict = response.parsed
    return {"same_speech": verdict.same_speech, "reason": verdict.reason, "model": settings.gemini_model}
