"""Narration, with more than one way to produce it.

edge-tts is the best free option -- natural voices, no API key, and it
reports word timings we use for caption highlighting. But it talks to an
undocumented Microsoft endpoint that answers 403 to datacenter IPs without
warning, which stopped the whole pipeline on 13 Oct: discovery, script and
shot list all succeeded and the run died here.

So narration now tries engines in order and uses the first that produces
audio. Quality degrades down the chain; a worse voice beats no reel, and
the caller is told which engine was used so it can say so.

  edge    natural, word timings, breaks when Microsoft feels like it
  gtts    Google Translate's voice, no key, needs network, no timings
  espeak  offline, robotic, cannot fail -- the floor
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import config


@dataclass
class Word:
    text: str
    start: float   # seconds
    end: float


class NarrationFailed(RuntimeError):
    """Every engine failed. Carries what each one said."""


# Which engine produced the last narration, for the caller to report.
last_engine = ""

# Voices to try before giving up on edge-tts. A 403 is usually about the
# endpoint rather than the voice, but a bad or retired voice name looks
# identical from here, so vary it.
_EDGE_FALLBACK_VOICES = [
    "en-US-AndrewMultilingualNeural",
    "en-US-ChristopherNeural",
    "en-GB-RyanNeural",
    "en-US-GuyNeural",
]


# ----------------------------------------------------------------- helpers

def _audio_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _usable(path: Path) -> bool:
    return path.exists() and path.stat().st_size >= 1000


def _estimate(text: str, total: float) -> list[Word]:
    """Spread words across a known duration, weighted by length.

    Used for engines that return audio but no word boundaries. Captions
    drift slightly but stay readable, which beats no captions at all.
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


def _rate_to_wpm(rate: str) -> int:
    """Words per minute for espeak.

    Derived from WORDS_PER_SECOND rather than from the edge-tts percentage,
    so the fallback speaks at the pace the script was actually written for
    instead of whatever a hardcoded base happened to give. `rate` is kept
    for signature compatibility and ignored.
    """
    return max(80, min(400, int(config.WORDS_PER_SECOND * 60)))


# ----------------------------------------------------------------- engines

async def _edge_once(text: str, mp3_path: Path, voice: str) -> list[Word]:
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=config.TTS_RATE)
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


def _engine_edge(text: str, mp3_path: Path) -> list[Word] | None:
    voices = [config.TTS_VOICE] + [
        v for v in _EDGE_FALLBACK_VOICES if v != config.TTS_VOICE]
    last = None
    for i, voice in enumerate(voices):
        for attempt in (1, 2):
            try:
                words = asyncio.run(_edge_once(text, mp3_path, voice))
            except Exception as exc:
                last = exc
                print(f"  ! edge-tts {voice} attempt {attempt}: "
                      f"{str(exc)[:120]}")
                time.sleep(2.0 * attempt)
                continue
            if _usable(mp3_path):
                if i:
                    print(f"  (fell back to voice {voice})")
                return words
            last = RuntimeError("no audio written")
            break                      # a silent success won't fix on retry
    if last:
        raise last
    raise RuntimeError("edge-tts produced no audio")


def _engine_gtts(text: str, mp3_path: Path) -> list[Word] | None:
    """Google Translate's voice. No key, no word timings."""
    from gtts import gTTS

    gTTS(text=text, lang="en", tld="com").save(str(mp3_path))
    if not _usable(mp3_path):
        raise RuntimeError("gTTS wrote no audio")
    return None                        # timings get estimated


def _engine_espeak(text: str, mp3_path: Path) -> list[Word] | None:
    """Offline fallback. Robotic, but it cannot be rate-limited or blocked."""
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise RuntimeError("espeak-ng is not installed")

    txt = mp3_path.with_suffix(".txt")
    wav = mp3_path.with_suffix(".espeak.wav")
    txt.write_text(text, encoding="utf-8")
    subprocess.run(
        [exe, "-v", "en-us", "-s", str(_rate_to_wpm(config.TTS_RATE)),
         "-f", str(txt), "-w", str(wav)],
        check=True, capture_output=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
         "-codec:a", "libmp3lame", "-q:a", "4", str(mp3_path)],
        check=True, capture_output=True)
    for tmp in (txt, wav):
        tmp.unlink(missing_ok=True)
    if not _usable(mp3_path):
        raise RuntimeError("espeak produced no audio")
    return None


_ENGINES = {
    "edge": _engine_edge,
    "gtts": _engine_gtts,
    "espeak": _engine_espeak,
}


# -------------------------------------------------------------------- api

def speak(text: str, mp3_path: Path) -> list[Word]:
    """Render narration to mp3 and return word-level timings."""
    global last_engine
    last_engine = ""

    text = (text or "").strip()
    if not text:
        raise RuntimeError(
            "Nothing to narrate -- the script came back empty. This usually "
            "means the script provider failed; check the [3/7] step above.")

    order = [n.strip() for n in config.TTS_PROVIDERS.split(",") if n.strip()]
    problems: list[str] = []

    for name in order:
        fn = _ENGINES.get(name)
        if not fn:
            problems.append(f"{name}: unknown engine")
            continue
        try:
            words = fn(text, mp3_path)
        except Exception as exc:
            problems.append(f"{name}: {str(exc)[:160]}")
            print(f"  ! {name} unavailable, trying the next engine")
            continue

        last_engine = name
        if name != order[0]:
            print(f"  ! narration came from '{name}', not '{order[0]}' -- "
                  f"the voice is worse than usual")
        if not words:
            total = _audio_duration(mp3_path)
            print(f"  ! no word timings from {name}; estimating from "
                  f"{total:.1f}s of audio")
            words = _estimate(text, total)

        # Close gaps so highlighting never flickers off between words.
        for i in range(len(words) - 1):
            words[i].end = max(words[i].end, words[i + 1].start)
        return words

    raise NarrationFailed(
        "No narration engine worked, so there is no audio to build on:\n  "
        + "\n  ".join(problems)
        + "\nInstall espeak-ng for an offline fallback that cannot be "
          "blocked: sudo apt-get install -y espeak-ng")


def duration(words: list[Word]) -> float:
    return words[-1].end if words else 0.0


if __name__ == "__main__":
    out = config.OUT_DIR / "tts_test.mp3"
    w = speak("Testing one two three. This is a narration check.", out)
    print(f"engine: {last_engine}")
    for x in w:
        print(f"{x.start:6.2f}-{x.end:6.2f}  {x.text}")
    print(f"\n{out} ({out.stat().st_size} bytes), {duration(w):.2f}s")
