#!/usr/bin/env python3
"""Let the backfill drain itself, once a day, on the leftovers.

The limit turned out to be PER CHANNEL, not a shared pool: Korea
uploaded two while science was being refused with "The user has
exceeded the number of videos they may upload." So there is nothing to
ration between the three repos and no rotation to schedule -- each one
simply needs to use its own daily allowance.

TIMING IS THE WHOLE TRICK. YouTube's day resets at Pacific midnight,
currently 07:00 UTC. Every one of science's five build slots falls
inside that window, the last at 04:47 UTC. Running the backfill at
05:31 UTC means the day's own fresh reels have already taken what they
need and the backfill spends only what is left -- the opposite of this
morning, when a manual backfill ran first and competed with them.

It is self-correcting. A refused upload records youtube_error, keeps
no youtube_id, and is picked up again tomorrow. So an over-ambitious
count costs a little runtime and nothing else, and the backlog drains
at exactly whatever rate YouTube allows.

Minutes are staggered between the repos (:31 and :41) because GitHub
throttles bursts of scheduled runs, which is what kept dropping builds
on 20 Oct.

BACKFILL_PER_DAY tunes it without touching code; 5 by default.

    python3 tools/fix_backfill_daily.py
"""
import pathlib
import subprocess
import sys

OLD_ON = """on:
  workflow_dispatch:
    inputs:
      count:
        description: "How many to upload this run"
        required: false
        default: "8"
"""

NEW_ON = """on:
  # After the day's last build (04:47 UTC) and before YouTube's quota
  # resets at Pacific midnight (07:00 UTC), so this spends the
  # allowance the fresh reels did not use rather than competing with
  # them. A refused upload is retried tomorrow by itself.
  schedule:
    - cron: "{minute} 5 * * *"       # {seoul} Seoul, on the day's leftovers
  workflow_dispatch:
    inputs:
      count:
        description: "How many to upload this run"
        required: false
        default: "8"
"""

OLD_N = '''          N="${{ github.event.inputs.count || '8' }}"
'''

NEW_N = '''          # A scheduled run has no inputs, so it falls through to
          # BACKFILL_PER_DAY and then to 5 -- small enough to sit
          # inside whatever the day's builds left behind.
          N="${{ github.event.inputs.count || vars.BACKFILL_PER_DAY || '5' }}"
'''


def main() -> None:
    p = pathlib.Path(".github/workflows/backfill.yml")
    if not p.exists():
        sys.exit("no backfill.yml here -- run tools/fix_backfill.py first")

    remote = subprocess.run(["git", "remote", "get-url", "origin"],
                            capture_output=True, text=True).stdout
    # k-newsreels must be tested first: "news-reels" is a substring of it.
    if "k-newsreels" in remote:
        minute, seoul = "41", "14:41"
    elif "news-reels" in remote:
        minute, seoul = "31", "14:31"
    else:
        sys.exit(f"  unrecognised remote {remote.strip()!r} -- NOT TOUCHED")

    src = p.read_text()
    if "schedule:" in src:
        print("  backfill.yml: already on a schedule")
        return
    if src.count(OLD_ON) != 1 or src.count(OLD_N) != 1:
        sys.exit("  backfill.yml: not the expected text -- NOT TOUCHED")

    new = src.replace(OLD_ON, NEW_ON.format(minute=minute, seoul=seoul))
    new = new.replace(OLD_N, NEW_N)

    try:
        import yaml
        doc = yaml.safe_load(new)
        steps = [s for j in doc["jobs"].values() for s in j["steps"]]
        bad = [i for i, s in enumerate(steps, 1)
               if not ({"run", "uses"} & set(s))]
        if bad:
            sys.exit(f"  step {bad} would have neither run nor uses -- NOT WRITTEN")
        crons = (doc.get(True) or doc.get("on") or {}).get("schedule") or []
        if len(crons) != 1:
            sys.exit(f"  expected 1 cron, got {len(crons)} -- NOT WRITTEN")
    except ImportError:
        pass

    p.write_text(new)
    print(f"  backfill.yml: daily at 05:{minute} UTC = {seoul} Seoul, "
          f"BACKFILL_PER_DAY or 5 per run")


if __name__ == "__main__":
    main()
