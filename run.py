#!/usr/bin/env python3
"""ScienceDaily -> illustrated 85s reel -> Telegram approval -> Instagram.

Local use:
    python run.py build                 # pick a story and build a reel
    python run.py build --story URL     # build one specific article
    python run.py list                  # what's waiting
    python run.py approve <slug>        # publish it now
    python run.py reject <slug>         # discard it

Called by CI:
    python run.py latest-slug           # slug of the newest pending reel
    python run.py set-video-url <slug> <url>
    python run.py notify <slug>         # send to Telegram with buttons
    python run.py poll                  # act on button presses
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import config
from core import captions, feed, publish, render, sources, visuals
from core import script as script_mod
from core.visuals import Shot

PENDING = config.STATE_DIR / "pending"
PENDING.mkdir(parents=True, exist_ok=True)


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return f"{datetime.now().strftime('%Y%m%d')}-{s[:44]}"


def _manifest_path(slug: str) -> Path:
    return PENDING / f"{slug}.json"


def _save(slug: str, data: dict) -> None:
    _manifest_path(slug).write_text(json.dumps(data, indent=2))


def _load(slug: str) -> dict:
    return json.loads(_manifest_path(slug).read_text())


# --------------------------------------------------------------------- build

def _build_one(args) -> int:
    print("\n[1/7] selecting story")
    if args.story:
        req = urllib.request.Request(args.story, headers={"User-Agent": feed.UA})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf8", "ignore")
        title = feed._clean(re.search(r"<title>(.*?)</title>", html, re.S).group(1))
        m = re.search(r'<meta name="description" content="(.*?)"', html, re.S)
        story = feed.Story(hashlib.sha1(args.story.encode()).hexdigest()[:16],
                           title, feed._clean(m.group(1)) if m else "",
                           args.story, "", "manual")
    else:
        already = set()
        for mf in PENDING.glob("*.json"):
            try:
                already.add(json.loads(mf.read_text()).get("story_uid"))
            except (json.JSONDecodeError, OSError):
                pass
        story = sources.pick_story(max_tier=args.max_tier,
                                   use_search=not args.no_search,
                                   exclude_uids=already)

    if not story:
        print("  no suitable story found -- nothing to do")
        return 0
    print(f"  {story.title}\n  {story.link}")

    slug = _slug(story.title)
    work = config.OUT_DIR / slug
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    article = getattr(story, "article", "") or ""
    if config.USE_FULL_ARTICLE and not article:
        print("\n[2/7] fetching article for source material")
        article = sources.fetch_article(story.link)
    print(f"\n[2/7] source material: {len(article.split())} words")

    tier = getattr(story, "tier", "safe")
    if tier != "safe":
        print(f"  ! this story is tier '{tier}' -- approval is required and "
              f"extra caveats are enforced")

    print("\n[3/7] writing script and shot list")
    guide_key = ("oddcrime" if getattr(story, "topic", "") == "oddcrime"
             else tier)
    sc = script_mod.generate(story.title, story.summary, article,
                             tier=guide_key)

    if args.hook:
        print(f"  using your hook instead of the generated one")
        sc.narration = sc.narration.replace(sc.hook, "", 1).strip()
        sc.hook = args.hook.strip()
        sc.narration = f"{sc.hook} {sc.narration}".strip()

    if not sc.shots and not args.allow_fallback:
        print("\n  ! The script provider failed, so this would be the plain "
              "fallback text with one generic image -- not worth posting.\n"
              "    Fix the provider above, or pass --allow-fallback to build "
              "it anyway.")
        shutil.rmtree(work, ignore_errors=True)
        return 1
    kcfg = sources.load_keywords()
    caption = script_mod.build_caption(
        sc, story.title, story.link,
        topic=getattr(story, 'topic', ''), cfg=kcfg)
    (work / "script.txt").write_text(sc.outline() + "\n")
    print(f"  {len(sc.shots)} shots planned")
    print(f"\n  HOOK: {sc.hook}")
    for i, alt in enumerate(sc.hook_options, 1):
        if alt.strip() and alt.strip() != sc.hook.strip():
            print(f"    alt {i}: {alt[:100]}")

    print("\n[4/7] generating narration")
    narration = sc.narration
    if config.CTA_TEXT.strip():
        narration = f"{narration} {config.CTA_TEXT.strip()}"
    audio = work / "narration.mp3"
    words = _speak(narration, audio)
    total = words[-1].end if words else 0.0
    print(f"  {total:.1f}s, {len(words)} words")

    print("\n[5/7] sourcing imagery")
    shots = [Shot(query=s.get("query", ""), cue=s.get("cue", ""))
             for s in sc.shots if s.get("query")]
    if not shots:
        shots = [Shot(query=" ".join(story.title.split()[:4]), cue="")]
    shots = visuals.align_shots(shots, words, total)

    before = len(shots)
    shots = visuals.split_long_shots(shots)
    avg = total / max(1, len(shots))
    print(f"  {before} beats -> {len(shots)} shots "
          f"(~{avg:.1f}s each)")

    used: set[str] = set()
    found = sum(visuals.resolve_shot(s, work, i, used)
                for i, s in enumerate(shots))
    print(f"  {found}/{len(shots)} images found")

    caption = script_mod.build_caption(
        sc, story.title, story.link, topic=getattr(story, "topic", ""),
        cfg=kcfg, credits=visuals.credits_block(shots))
    (work / "caption.txt").write_text(caption)
    print(f"  caption: {len(caption)} chars")

    print("\n[6/7] rendering")
    captions.build_srt(words, work / "captions.srt")
    ass = captions.build_ass(words, work / "captions.ass")
    sequence = render.build_sequence(shots, work)
    music = render.pick_music(getattr(story, "topic", ""))
    print(f"  music: {music.name}" if music
          else "  music: none (drop files in assets/music/)")
    reel = render.finish(sequence, audio, ass, work / "reel.mp4",
                         kicker=args.kicker, music=music, workdir=work)
    dur = render.probe_duration(reel)
    print(f"  {reel} ({reel.stat().st_size/1e6:.1f} MB, {dur:.1f}s)")

    _save(slug, {
        "slug": slug,
        "title": story.title,
        "link": story.link,
        "story_uid": story.uid,
        "caption": caption,
        "duration": dur,
        "shots_found": f"{found}/{len(shots)}",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "video_url": "",
    })

    print("\n[7/7] awaiting approval")
    if args.auto and tier != "safe":
        print(f"  ! refusing --auto for a '{tier}' story. Breaking news is "
              f"wrong often enough in the first hours that this one needs "
              f"your eyes. Approve it manually.")
        return 0
    if args.auto:
        return _publish(slug)
    print(f"  python run.py approve {slug}")
    return 0


def cmd_build(args) -> int:
    """Build one reel, or several in a row.

    Each pass marks its story seen only on publish, but the pending
    manifest keeps it out of the next pick, so a run of N gives N
    different stories.
    """
    count = max(1, int(getattr(args, "count", 1) or 1))
    made = 0
    for i in range(count):
        if count > 1:
            print(f"\n{'='*58}\n  REEL {i + 1} of {count}\n{'='*58}")
        rc = _build_one(args)
        if rc != 0:
            print(f"  stopping after {made} reel(s)")
            break
        made += 1
    print(f"\n  built {made} reel(s)")
    return 0 if made else 1


def _speak(text: str, path: Path):
    try:
        from core import tts
    except ImportError:
        print("  ! edge-tts missing: pip install edge-tts")
        raise
    return tts.speak(text, path)


# ------------------------------------------------------------------ publish

def _publish(slug: str) -> int:
    man = _load(slug)
    url = man.get("video_url") or ""
    if not url:
        reel = config.OUT_DIR / slug / "reel.mp4"
        if not reel.exists():
            print(f"  ! no video_url and no local file for {slug}")
            return 1
        url = publish.host(reel)
        man["video_url"] = url
        _save(slug, man)

    media_id = publish.publish_reel(url, man["caption"])
    man.update(status="published", media_id=media_id,
               published_at=datetime.now(timezone.utc).isoformat())
    _save(slug, man)
    feed.mark_seen(man["story_uid"])
    print(f"  published {media_id}")
    return 0


def cmd_approve(args) -> int:
    if not _manifest_path(args.slug).exists():
        print(f"no such reel: {args.slug}")
        return 1
    return _publish(args.slug)


def cmd_reject(args) -> int:
    if not _manifest_path(args.slug).exists():
        print(f"no such reel: {args.slug}")
        return 1
    man = _load(args.slug)
    feed.mark_seen(man["story_uid"])
    man["status"] = "rejected"
    _save(args.slug, man)
    print(f"rejected {args.slug}")
    return 0


# ----------------------------------------------------------------- CI hooks

def cmd_latest_slug(args) -> int:
    pend = [json.loads(p.read_text()) for p in PENDING.glob("*.json")]
    pend = [m for m in pend if m.get("status") == "pending"]
    if not pend:
        return 0
    print(max(pend, key=lambda m: m["built_at"])["slug"])
    return 0


def cmd_pending_slugs(args) -> int:
    rows = []
    for mf in PENDING.glob("*.json"):
        try:
            m = json.loads(mf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if m.get("status") != "pending" or m.get("video_url"):
            continue
        # Only list reels whose video actually exists here. A manifest can
        # outlive its file (built on another machine, out/ is gitignored).
        if not (config.OUT_DIR / m["slug"] / "reel.mp4").exists():
            continue
        rows.append((m["built_at"], m["slug"]))
    for _, slug in sorted(rows):
        print(slug)
    return 0


def cmd_set_video_url(args) -> int:
    man = _load(args.slug)
    man["video_url"] = args.url
    _save(args.slug, man)
    print(f"video_url set for {args.slug}")
    return 0


def cmd_notify(args) -> int:
    from core import telegram
    if not telegram.enabled():
        print("  telegram not configured, skipping")
        return 0
    man = _load(args.slug)
    reel = config.OUT_DIR / args.slug / "reel.mp4"
    telegram.send_for_approval(reel, args.slug, man["title"],
                               man["caption"], man["duration"])
    print(f"  sent {args.slug} for approval")
    return 0


def cmd_poll(args) -> int:
    from core import telegram
    if not telegram.enabled():
        print("  telegram not configured")
        return 0

    decisions = telegram.fetch_decisions()
    if not decisions:
        print("  no new decisions")
        return 0

    print(f"  {len(decisions)} decision(s): "
          + ", ".join(f"{a}:{s_[:28]}" for a, s_, _, _ in decisions))

    for action, slug, chat_id, msg_id in decisions:
        if not _manifest_path(slug).exists():
            print(f"  ! unknown reel: {slug}")
            telegram.mark_done(chat_id, msg_id, f"Unknown reel: {slug}")
            continue

        man = _load(slug)
        if man.get("status") != "pending":
            print(f"  i already {man.get('status')}: {slug}")
            telegram.mark_done(chat_id, msg_id,
                               f"Already {man.get('status')}: {slug}")
            continue

        if action == "no":
            print(f"  rejecting {slug}")
            cmd_reject(argparse.Namespace(slug=slug))
            telegram.mark_done(chat_id, msg_id, f"Rejected: {man['title'][:80]}")
            continue

        try:
            print(f"  approving {slug}")
            _publish(slug)
            telegram.mark_done(chat_id, msg_id,
                               f"Posted to Instagram: {man['title'][:80]}")
        except Exception as exc:
            telegram.mark_done(chat_id, msg_id, f"Publish failed: {exc}")
            return 1
    return 0



# ---------------------------------------------------------------- keywords

def cmd_keywords(args) -> int:
    cfg = sources.load_keywords()
    topics = cfg.setdefault("topics", {})

    if args.action == "list":
        enabled = cfg.get("enabled_topics", [])
        for name, spec in topics.items():
            on = "ON " if name in enabled else "off"
            tier = spec.get("tier", "safe")
            wait = sources.MIN_AGE_HOURS.get(tier, 0)
            delay = f", waits {wait}h" if wait else ""
            print(f"\n  [{on}] {name}  (tier: {tier}{delay})")
            for k in spec.get("keywords", []):
                print(f"         - {k}")
            for f in spec.get("feeds", []):
                print(f"         feed: {f}")
        print(f"\n  blocked terms: {', '.join(cfg.get('blocked_terms', []))}")
        print(f"\n  enabled topics: {', '.join(enabled) or '(none)'}")
        return 0

    if args.action in ("add", "remove"):
        if not args.topic or not args.words:
            print("need --topic and one or more keywords")
            return 1
        spec = topics.setdefault(args.topic, {"tier": "sensitive",
                                              "keywords": [], "feeds": []})
        kws = spec.setdefault("keywords", [])
        for w in args.words:
            if args.action == "add" and w not in kws:
                kws.append(w)
            elif args.action == "remove" and w in kws:
                kws.remove(w)
        sources.save_keywords(cfg)
        print(f"{args.topic}: {len(kws)} keywords")
        return 0

    if args.action in ("enable", "disable"):
        if not args.topic:
            print("need --topic")
            return 1
        enabled = cfg.setdefault("enabled_topics", [])
        if args.action == "enable" and args.topic not in enabled:
            enabled.append(args.topic)
            tier = topics.get(args.topic, {}).get("tier", "safe")
            if tier != "safe":
                print(f"  note: '{args.topic}' is tier '{tier}' -- it will "
                      f"never auto-post, and waits "
                      f"{sources.MIN_AGE_HOURS.get(tier)}h before a story "
                      f"is eligible.")
        elif args.action == "disable" and args.topic in enabled:
            enabled.remove(args.topic)
        sources.save_keywords(cfg)
        print(f"enabled: {', '.join(enabled) or '(none)'}")
        return 0

    if args.action == "preview":
        found = sources.gather(cfg, use_search=not args.no_search)
        print(f"\n  {len(found)} stories matched:\n")
        for st in found[:20]:
            print(f"  {st.score:5.1f} [{st.topic}/{st.tier}] {st.title[:62]}")
            print(f"        {st.publisher or '?'} | matched: "
                  f"{', '.join(st.matched[:4])}")
        return 0

    return 1


def cmd_list(args) -> int:
    rows = sorted(PENDING.glob("*.json"))
    if not rows:
        print("nothing built yet")
        return 0
    for p in rows:
        m = json.loads(p.read_text())
        print(f"  [{m['status']:9}] {m['slug']}\n              {m['title'][:60]}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build")
    b.add_argument("--story"); b.add_argument("--auto", action="store_true")
    b.add_argument("--kicker")
    b.add_argument("--allow-fallback", action="store_true",
                   help="build even if the script provider failed")
    b.add_argument("--hook",
                   help="write the opening line yourself, overriding the model")
    b.add_argument("--max-tier", default="safe",
                   choices=["safe", "caution", "sensitive"],
                   help="highest risk tier to consider (default: safe)")
    b.add_argument("--no-search", action="store_true",
                   help="use only the listed RSS feeds, skip Google News")
    b.add_argument("--count", type=int, default=1,
                   help="how many reels to build in this run")
    b.set_defaults(fn=cmd_build)

    for name, fn in (("approve", cmd_approve), ("reject", cmd_reject),
                     ("notify", cmd_notify)):
        s = sub.add_parser(name); s.add_argument("slug"); s.set_defaults(fn=fn)

    s = sub.add_parser("set-video-url")
    s.add_argument("slug"); s.add_argument("url"); s.set_defaults(fn=cmd_set_video_url)

    for name, fn in (("list", cmd_list), ("poll", cmd_poll),
                     ("latest-slug", cmd_latest_slug),
                     ("pending-slugs", cmd_pending_slugs)):
        s = sub.add_parser(name); s.set_defaults(fn=fn)

    k = sub.add_parser("keywords")
    k.add_argument("action",
                   choices=["list", "add", "remove", "enable", "disable",
                            "preview"])
    k.add_argument("words", nargs="*")
    k.add_argument("--topic")
    k.add_argument("--no-search", action="store_true")
    k.set_defaults(fn=cmd_keywords)

    args = p.parse_args()
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
