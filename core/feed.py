"""Fetch ScienceDaily RSS and pick an unseen story. Standard library only."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import config

SEEN_PATH = config.STATE_DIR / "seen.json"
UA = "Mozilla/5.0 (compatible; scidaily-reels/1.0)"


@dataclass
class Story:
    uid: str
    title: str
    summary: str
    link: str
    published: str
    source_feed: str

    def to_dict(self) -> dict:
        return asdict(self)


def _load_seen() -> set[str]:
    if not SEEN_PATH.exists():
        return set()
    try:
        return set(json.loads(SEEN_PATH.read_text()))
    except (json.JSONDecodeError, OSError):
        return set()


def mark_seen(uid: str) -> None:
    seen = _load_seen()
    seen.add(uid)
    # Keep the file from growing forever.
    SEEN_PATH.write_text(json.dumps(sorted(seen)[-2000:], indent=0))


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = (text.replace("&amp;", "&").replace("&#039;", "'")
                .replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")
                .replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", text).strip()


def _fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _parse_feed(xml_bytes: bytes, feed_url: str) -> list[Story]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    stories: list[Story] = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title", ""))
        summary = _clean(item.findtext("description", ""))
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if not (title and summary and link):
            continue
        uid = hashlib.sha1(link.encode()).hexdigest()[:16]
        stories.append(Story(uid, title, summary, link, pub, feed_url))
    return stories


def fetch_article_text(url: str, max_chars: int = 9000) -> str:
    """Pull the body text of a ScienceDaily article.

    The RSS description is only a couple of sentences -- far too thin to
    build a 90-second explainer from without the model inventing detail.
    The article page carries the full press release, which gives the script
    real material: what was measured, how, by whom, and the caveats.

    This text is source material for an original rewrite, never for reuse.
    """
    try:
        html = _fetch(url).decode("utf-8", "ignore")
    except Exception as exc:
        print(f"  ! could not fetch article body: {exc}")
        return ""

    # ScienceDaily wraps the release body in #story_text / #text.
    m = re.search(r'<div[^>]+id="story_text".*?>(.*?)</div>\s*<div[^>]+id="story_source"',
                  html, re.S)
    if not m:
        m = re.search(r'<div[^>]+id="text"[^>]*>(.*?)</div>', html, re.S)
    if not m:
        paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.S)
        body = " ".join(paras[:14])
    else:
        body = m.group(1)

    # Drop boilerplate blocks before cleaning tags.
    body = re.sub(r"<(script|style|aside|figure)[^>]*>.*?</\1>", " ", body, flags=re.S)

    text = _clean(body)
    text = re.sub(r"(Story Source|Journal Reference|Cite This Page|RELATED).*$",
                  "", text, flags=re.S | re.I)
    return text[:max_chars].strip()


def _age_days(pub: str) -> float:
    try:
        dt = parsedate_to_datetime(pub)
    except (TypeError, ValueError):
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


def pick_story(feeds: list[str] | None = None) -> Story | None:
    """Return the newest story not already used, or None."""
    feeds = feeds or config.FEEDS
    seen = _load_seen()
    candidates: list[Story] = []

    for url in feeds:
        try:
            candidates.extend(_parse_feed(_fetch(url), url))
        except Exception as exc:  # network / parse failures are non-fatal
            print(f"  ! feed failed {url}: {exc}")

    fresh = [
        s for s in candidates
        if s.uid not in seen and _age_days(s.published) <= config.MAX_STORY_AGE_DAYS
    ]
    if not fresh:
        return None

    fresh.sort(key=lambda s: _age_days(s.published))
    return fresh[0]


if __name__ == "__main__":
    s = pick_story()
    print(json.dumps(s.to_dict(), indent=2) if s else "no new stories")
