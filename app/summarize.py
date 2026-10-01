"""Transcript -> meeting minutes, via a local Ollama model.

127.0.0.1:11434 is the only network address this app ever talks to. Nothing
leaves the machine.

Long meetings are summarized in batches: the transcript is split on line
boundaries, each segment is digested on its own, and the digests are combined
into the minutes. Every line of the meeting reaches the model. Batches run
sequentially — on CPU one request already uses every core, so running two at
once finishes no sooner and doubles the memory.
"""

import json
import os
import re
import urllib.error
import urllib.request

OLLAMA = os.environ.get("MEETINGAI_OLLAMA", "http://127.0.0.1:11434")
# Small enough for any 16GB laptop: about 1GB, and roughly 5x faster than
# qwen3.5:4b, which writes more reliable minutes (owners, details) for those who
# can spare ~4GB of RAM -- switchable in Settings. Not qwen3:4b: that tag is a
# thinking-only build that ignores think=false.
DEFAULT_MODEL = os.environ.get("MEETINGAI_LLM", "qwen3.5:0.8b")

# CPU generation is slow; a long transcript can legitimately take many minutes.
TIMEOUT = int(os.environ.get("MEETINGAI_LLM_TIMEOUT", 1800))

NUM_CTX = int(os.environ.get("MEETINGAI_NUM_CTX", 8192))
RESERVED_TOKENS = 1200        # instructions + room to write the answer
CHARS_PER_TOKEN = 4           # rough for English; timestamps are worse
SEGMENT_FRACTION = 0.6        # headroom, because the estimate above is optimistic
MAX_DEPTH = 3                 # digests of digests of digests; converges long before this

# Batching only pays if a digest is much smaller than the segment it came from.
# Generation on CPU runs around 7 tok/s, so output length IS the runtime: an
# unbounded digest of a 450-line segment measured at ~15 minutes on its own.
# Both limits below are load-bearing — the prompt asks, num_predict enforces.
DIGEST_BULLETS = 12
DIGEST_MAX_TOKENS = 420
MINUTES_MAX_TOKENS = 900

# The transcript goes FIRST and the instructions LAST. If a prompt ever
# overflows the context window, Ollama discards the oldest tokens — so this
# ordering spends an overflow on transcript rather than on the output format.
MINUTES_PROMPT = """{content}

----------

You are writing the minutes of the meeting recorded above.

{speakers}

Write Markdown with exactly these sections, in this order:

## Summary
Three to six sentences on what the meeting was about and what came of it.

## Decisions
What was actually decided. One bullet each. If nothing was decided, write "None".

## Action Items
One bullet each, as "- Owner — task (deadline if stated)". Use "Unassigned"
when the owner is unclear. If there are none, write "None".

## Open Questions
Anything raised and left unresolved. If there are none, write "None".

Rules: use only what is written above, never guess at anything that was not
said, do not add sections beyond the four above, and write entirely in English.
"""

TWO_SIDES = """"You" is the person whose microphone recorded it. "Them" is everyone else,
heard through the computer speakers. Individual remote speakers are not
distinguished, so never invent names; use "You" or "Them" as owners."""

# Once speakers.py has told the remote voices apart and people have named them.
NAMED_SPEAKERS = """Each line starts with who spoke: a person's name, "You" for whoever
recorded the meeting, or "Speaker 1", "Speaker 2" for voices nobody has named.
Use exactly those labels for who decided what and who owns each action item.
Never invent names that do not appear in the transcript."""

DIGEST_PROMPT = """{content}

----------

The text above is one part of a longer meeting.

Write AT MOST {max_bullets} bullets covering only what would matter in the
minutes: decisions made, tasks assigned and who owns them, and questions left
unresolved. Add the main topic as one further bullet if nothing was decided.

Hard rules:
- At most {max_bullets} bullets. Fewer is better. Merge related points.
- One short line each, under 20 words. Start each with its timestamp.
- Do not repeat the discussion line by line. This is a condensation, not a copy.
- No preamble, no closing remarks, no headings.
- Never invent anything not written above. Write entirely in English.
"""

TRUNCATION_NOTE = (
    "> **Note:** this meeting was too long to summarize completely, so the "
    "earliest {dropped} characters were left out. The full transcript is in "
    "`transcript.txt`.\n\n"
)


class SummarizeError(RuntimeError):
    pass


def _post(path: str, payload: dict, timeout: int = TIMEOUT) -> dict:
    req = urllib.request.Request(
        f"{OLLAMA}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def list_models() -> list[str]:
    """Installed Ollama models, for the settings dropdown."""
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return []
    return sorted(m["name"] for m in data.get("models", []))


def strip_thinking(text: str) -> str:
    """Reasoning models (qwen3, deepseek-r1) emit <think> blocks. Drop them."""
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()


def budget_chars() -> int:
    """How much text fits in one call, in characters."""
    return max(1000, (NUM_CTX - RESERVED_TOKENS) * CHARS_PER_TOKEN)


def split_transcript(text: str, budget: int) -> list[str]:
    """Split on line boundaries so no line is ever cut in half or dropped.

    A single line longer than the budget becomes its own segment rather than
    being discarded — losing a line is the one thing this must never do.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    segments, current, size = [], [], 0

    for line in lines:
        cost = len(line) + 1
        if current and size + cost > budget:
            segments.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += cost

    if current:
        segments.append("\n".join(current))
    return segments


def _ask(prompt: str, model: str, send, max_tokens: int) -> str:
    try:
        data = send("/api/generate", {
            "model": model,
            "prompt": prompt,
            "stream": False,
            # Thinking models (qwen3, qwen3.5) otherwise reason first: on CPU
            # that costs far more than the answer, and with num_predict capped
            # the reasoning can use it all, leaving the minutes empty. Measured
            # on a 6KB transcript with qwen3.5:0.8b: 41s and nothing, vs 9s.
            "think": False,
            "options": {"num_ctx": NUM_CTX, "temperature": 0.2,
                        "num_predict": max_tokens},
        })
    except urllib.error.HTTPError as e:
        raise SummarizeError(
            f"Ollama rejected the request ({e.code}). Is the model {model!r} installed? "
            f"Run: ollama pull {model}"
        ) from e
    except (urllib.error.URLError, OSError) as e:
        raise SummarizeError(
            f"Could not reach Ollama at {OLLAMA} ({e}). Is it running?"
        ) from e

    out = strip_thinking(data.get("response", ""))
    if not out:
        if data.get("thinking"):
            raise SummarizeError(
                f"{model} spent its whole answer thinking and wrote no minutes. "
                f"Try a model that can skip thinking, or a larger one.")
        raise SummarizeError(f"{model} returned nothing")
    return out


def _reduce(content: str, model: str, send, progress, depth: int,
            speakers: str = TWO_SIDES) -> str:
    """Digest each segment, then write the minutes from the digests."""
    budget = budget_chars()
    segments = split_transcript(content, int(budget * SEGMENT_FRACTION))
    total = len(segments) + 1

    digests = []
    for i, segment in enumerate(segments, 1):
        if progress:
            progress(i, total)
        digests.append(_ask(
            DIGEST_PROMPT.format(content=segment, max_bullets=DIGEST_BULLETS),
            model, send, DIGEST_MAX_TOKENS))

    combined = "\n\n".join(digests)

    # Digests of a very long meeting can themselves overflow. Reduce again
    # rather than trim — each pass shrinks the text a lot, so this converges.
    if len(combined) > budget and depth < MAX_DEPTH:
        return _reduce(combined, model, send, progress, depth + 1, speakers)

    dropped = 0
    if len(combined) > budget:
        # Only reachable if the model refuses to compress at all. Visible
        # failure beats a silent one.
        dropped = len(combined) - budget
        combined = combined[-budget:]

    if progress:
        progress(total, total)
    minutes = _ask(MINUTES_PROMPT.format(content=combined, speakers=speakers), model, send,
                   MINUTES_MAX_TOKENS)
    return TRUNCATION_NOTE.format(dropped=dropped) + minutes if dropped else minutes


def summarize(transcript: str, model: str | None = None, post=None,
              progress=None, speakers: str | None = None) -> str:
    """Minutes as Markdown. Raises SummarizeError if Ollama cannot be reached.

    `progress(done, total)` is called before each model request, so a long
    meeting can show "Summarizing 3/7" rather than an opaque wait.
    """
    transcript = (transcript or "").strip()
    if not transcript:
        # Nothing was said; do not spend minutes of CPU discovering that.
        return "## Summary\n\nNo speech was detected in this recording.\n"

    model = model or DEFAULT_MODEL
    speakers = speakers or TWO_SIDES
    send = post or _post

    if len(transcript) <= budget_chars():
        if progress:
            progress(1, 1)
        return _ask(MINUTES_PROMPT.format(content=transcript, speakers=speakers), model, send,
                    MINUTES_MAX_TOKENS)

    return _reduce(transcript, model, send, progress, depth=0, speakers=speakers)
