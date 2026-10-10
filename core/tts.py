"""Narration via edge-tts: free, no API key, and it reports word timings."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import config


@dataclass
class Word:
    text: str
    start: float   # seconds
    end: float


async def _synthesize(text: str, mp3_path: Path) -> list[Word]:
    import edge_tts  # imported lazily so the rest of the pipeline works without it

    comm = edge_tts.Communicate(text, config.TTS_VOICE, rate=config.TTS_RATE)
    words: list[Word] = []

    with open(mp3_path, "wb") as fh:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                # edge-tts reports in 100-nanosecond ticks
                start = chunk["offset"] / 1e7
                dur = chunk["duration"] / 1e7
                words.append(Word(chunk["text"], start, start + dur))

    return words


def _audio_duration(path: Path) -> float:
    import subprocess
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _estimate(text: str, total: float) -> list[Word]:
    """Spread words across a known duration, weighted by length.

    Used when the voice returns audio but no word boundaries -- some voices
    and some edge-tts versions don't emit them. Captions drift slightly but
    stay readable, which beats shipping a video with no captions at all.
    """
    tokens = text.split()
    if not tokens or total <= 0:
        return []

    weights = []
    for tok in tokens:
        w = len(tok) + 2
        if tok.endswith((".", "!", "?")):
            w += 6
        elif tok.endswith((",", ";", ":")):
            w += 3
        weights.append(w)

    scale = total / sum(weights)
    words, t = [], 0.0
    for tok, w in zip(tokens, weights):
        d = w * scale
        words.append(Word(tok, t, t + d))
        t += d
    return words


def speak(text: str, mp3_path: Path) -> list[Word]:
    """Render narration to mp3; return word-level timings."""
    text = (text or "").strip()
    if not text:
        raise RuntimeError(
            "Nothing to narrate -- the script came back empty. This usually "
            "means the script provider failed; check the [3/7] step above.")

    words = asyncio.run(_synthesize(text, mp3_path))

    if not mp3_path.exists() or mp3_path.stat().st_size < 1000:
        raise RuntimeError(
            f"edge-tts produced no audio for {len(text.split())} words. "
            f"Check the network and that TTS_VOICE is valid "
            f"(edge-tts --list-voices).")

    if not words:
        total = _audio_duration(mp3_path)
        print(f"  ! voice returned no word timings; estimating from "
              f"{total:.1f}s of audio")
        words = _estimate(text, total)

    # Close gaps so highlighting never flickers off between words.
    for i in range(len(words) - 1):
        words[i].end = max(words[i].end, words[i + 1].start)

    return words


def duration(words: list[Word]) -> float:
    return words[-1].end if words else 0.0


if __name__ == "__main__":
    out = config.OUT_DIR / "tts_test.mp3"
    w = speak("Testing one two three. This is a narration check.", out)
    for x in w:
        print(f"{x.start:6.2f}-{x.end:6.2f}  {x.text}")
    print(f"\n{out} ({out.stat().st_size} bytes), {duration(w):.2f}s")
