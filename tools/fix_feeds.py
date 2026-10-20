#!/usr/bin/env python3
"""Make ScienceDaily's top news the science channel's primary source.

The channel was reading three feeds: botany, microbiology, and
agriculture_and_food. That is a narrow corner of ScienceDaily, and
pick_story only accepts stories that are unseen AND under
MAX_STORY_AGE_DAYS old -- so a thin pool empties, pick_story returns
None, the build exits NO_STORY, the run goes green, and the channel
goes quiet with nothing to show why. That is a strong candidate for the
silent stretches rather than anything being broken.

Primary becomes https://www.sciencedaily.com/rss/top.xml -- the feed
behind sciencedaily.com/news/top/. It carries 60 items, refreshed
constantly, already filtered for interest by their editors, and spans
neuroscience, medicine, physics, space and climate instead of three
plant-science categories.

Four category top feeds are added behind it for breadth. pick_story
MERGES every feed and then sorts by age, so additional feeds make the
pool fresher, never noisier, and any feed that 403s is skipped rather
than fatal.

The original three stay, at the end, as depth: they surface agriculture
and botany stories the top feeds rarely carry.

top/society.xml is deliberately NOT included. It is the least
scientific of the category feeds and the most likely to carry political
and contentious social material, which on an unattended channel is risk
without benefit.

    python3 tools/fix_feeds.py
"""
import ast
import pathlib
import sys

OLD = '''FEEDS = [
    "https://www.sciencedaily.com/rss/plants_animals/botany.xml",
    "https://www.sciencedaily.com/rss/plants_animals/microbiology.xml",
    "https://www.sciencedaily.com/rss/plants_animals/agriculture_and_food.xml",
]
'''

NEW = '''FEEDS = [
    # PRIMARY. The feed behind sciencedaily.com/news/top/ -- their own
    # editorial top-news selection, 60 items, constantly refreshed.
    "https://www.sciencedaily.com/rss/top.xml",

    # Breadth, so the pool cannot empty. pick_story merges every feed,
    # drops anything already seen or older than MAX_STORY_AGE_DAYS, then
    # takes the newest survivor -- so more feeds means fresher stories,
    # not noisier ones, and a feed that fails is skipped, not fatal.
    "https://www.sciencedaily.com/rss/top/science.xml",
    "https://www.sciencedaily.com/rss/top/health.xml",
    "https://www.sciencedaily.com/rss/top/technology.xml",
    "https://www.sciencedaily.com/rss/top/environment.xml",

    # Depth. These three were the whole diet before, which is why the
    # channel kept running dry; they stay because they surface
    # agriculture and botany work the top feeds rarely reach.
    "https://www.sciencedaily.com/rss/plants_animals/botany.xml",
    "https://www.sciencedaily.com/rss/plants_animals/microbiology.xml",
    "https://www.sciencedaily.com/rss/plants_animals/agriculture_and_food.xml",

    # top/society.xml is left out on purpose: least scientific of the
    # category feeds, most likely to carry political and contentious
    # social material, which an unattended channel does not want.
]
'''


def main() -> None:
    p = pathlib.Path("config.py")
    if not p.exists() or not pathlib.Path("core/feed.py").exists():
        sys.exit("run this in the science repo (~/singlescape/scidaily-reels)")

    src = p.read_text()
    if "plants_animals/botany" not in src and "rss/top.xml" not in src:
        sys.exit("  this is not the science repo -- its FEEDS do not look "
                 "like ScienceDaily's. Nothing touched.")
    if "rss/top.xml" in src:
        print("  config.py: already on the top-news feed")
        return
    if src.count(OLD) != 1:
        sys.exit("  config.py: the FEEDS block is not there verbatim "
                 "-- NOT TOUCHED, edit it by hand")

    new = src.replace(OLD, NEW)
    try:
        ast.parse(new)
    except SyntaxError as exc:
        sys.exit(f"  config.py: would not parse ({exc}) -- NOT WRITTEN")

    # Sanity: the list must still be a list of strings, and must have
    # grown rather than replaced.
    ns = {}
    try:
        exec(compile(ast.parse(NEW), "<feeds>", "exec"), ns)
    except Exception as exc:
        sys.exit(f"  could not evaluate the new FEEDS ({exc}) -- NOT WRITTEN")
    feeds = ns["FEEDS"]
    if not (isinstance(feeds, list) and len(feeds) == 8
            and all(isinstance(f, str) and f.startswith("https://") for f in feeds)):
        sys.exit("  the new FEEDS list failed its own check -- NOT WRITTEN")

    p.write_text(new)
    print(f"  config.py: 3 feeds -> {len(feeds)}, primary is rss/top.xml")
    for f in feeds:
        print(f"      {f}")


if __name__ == "__main__":
    main()
