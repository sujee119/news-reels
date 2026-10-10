# news-reels

Keyword-driven news → illustrated ~85-second explainer → Telegram approval → Instagram Reels.

Runs itself on GitHub Actions. You tap one button on your phone; it posts.

---

## How it runs

```
         ┌─ build.yml (daily cron) ──────────────────────────┐
         │  RSS → full article → script + shot list          │
         │  → narration (edge-tts) → stock images per beat   │
         │  → Ken Burns + crossfade → captions burned in     │
         │  → upload as GitHub Release asset (public URL)    │
         │  → Telegram: video + [Approve] [Reject]           │
         └───────────────────────────────────────────────────┘
                              ↓  you tap a button
         ┌─ poll.yml (every 5 min) ──────────────────────────┐
         │  getUpdates → Approve? → Instagram 3-step publish │
         └───────────────────────────────────────────────────┘

         refresh-token.yml (monthly) keeps the IG token alive
```

Nothing posts without your tap. There is no `--auto` in the workflows.

---

## The visuals

Each reel is a sequence of 9–12 shots, not one background. The script
itself decides what's on screen: it emits a shot list of
`{cue, query}` pairs, where `cue` is a phrase from its own narration and
`query` is a stock-photo search term. The cue is matched against the TTS
word timings, so images land on the words they illustrate.

Images come from four sources, tried in order. **The first two need no API
key**, so the pipeline works out of the box:

| Source | Key | Good for |
|---|---|---|
| Pexels | free key | best-looking photography, tried first when keyed |
| Openverse | none | broad CC-licensed library |
| Wikimedia Commons | none | species, anatomy, instruments, specimens |
| Pixabay | free key | general fallback |

Only `CC0`, `PDM`, `CC BY` and `CC BY-SA` are accepted — non-commercial
licences are skipped, since an Instagram account shouldn't inherit those
terms. Every image used is credited automatically in the post caption.

A shot whose image can't be found becomes a plain dark card rather than
failing the render. If you see several, the script's queries were too
abstract — they're prompted toward photographable subjects for this reason.

### Cut rate

The script supplies 9–12 beats, but a beat held for 8 seconds is a
slideshow. Any beat longer than `MAX_SHOT_SECONDS` (default 4.0) is split
into sub-shots, each pulling a *different* candidate image for the same
query, so an 85s reel ends up around 20–25 shots at ~3s each.

Each shot gets one of four Ken Burns moves, sized so the full movement
completes within that shot's own length — short shots move as far as long
ones. Transitions are mostly dissolves with occasional wipes.

Tuning knobs in `config.py`:

```python
MAX_SHOT_SECONDS = 4.0    # lower = faster cutting
MIN_SHOT_SECONDS = 1.6    # floor, stops strobing
KENBURNS_ZOOM    = 0.22   # higher = more movement
SHOT_TRANSITION  = 0.40
```

---

## Background music

Drop tracks into `assets/music/`. One is chosen per video, looped, faded,
and **ducked under the narration** using a sidechain compressor — the bed
drops when the voice speaks and lifts in the gaps, rather than sitting at
a fixed low volume.

Nothing is downloaded automatically, deliberately. Music licensing is the
fastest way to get a channel muted or struck, and "free to download" often
doesn't mean "free to use on monetised social video". `assets/music/README.md`
lists safe sources — Pixabay Music is the easiest (no attribution, commercial
use allowed).

Do **not** use tracks from Instagram's in-app music library here: those are
licensed for use inside the app, not for a video you upload with the track
already mixed in.

```python
MUSIC_GAIN       = 0.16   # bed level, 0.0-1.0
MUSIC_DUCK_RATIO = 8      # higher = ducks harder under speech
```

Set `MUSIC_ENABLED=0` to turn it off.

---

## The follow card

Every reel ends with a spoken line plus a card:

```python
CTA_TEXT = "Follow for more science, every day."
CTA_CARD = "FOLLOW\nfor more science"
```

The spoken line is appended deterministically after the script is written,
not asked of the model — so it's identical every time and can't drift into
something odd. Set either to an empty string to drop it.

---

## Script structure

Six fixed sections, so the viewer learns the study rather than being teased:

| Section | Time | Job |
|---|---|---|
| Hook | 0–5s | Concrete image, one real detail withheld |
| Groundwork | 5–25s | The idea they need first, assuming zero background |
| Method | 25–45s | What the researchers actually did |
| Findings | 45–68s | The result, paying off the hook |
| So what | 68–82s | What it changes — and what it doesn't |
| Button | 82–85s | One line that lands it |

The RSS blurb is ~65 words; an 85-second script is ~230. The pipeline
fetches the full article first, because a model asked to triple its source
material invents the difference.

---

## Setup

### 1. Fork / push this repo

Make it **public** if you want the release-asset hosting to work — Instagram
fetches the video by URL and can't authenticate to a private repo. For a
private repo, use Cloudflare R2 instead (`HOST_PROVIDER=r2`).

### 2. Telegram bot

1. Message `@BotFather`, send `/newbot`, copy the token.
2. Send your new bot any message.
3. Get your chat id:
   `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"` → `result[0].message.chat.id`

Do **not** set a webhook on this bot — `poll.yml` uses `getUpdates`, and
the two are mutually exclusive.

### 3. Instagram

1. Convert the account to **Business** or **Creator**. Personal accounts
   cannot use the publishing endpoints at all.
2. Link it to a Facebook Page.
3. Create an app at developers.facebook.com, add the Instagram product.
4. Scopes: `instagram_basic`, `instagram_content_publish`,
   `pages_read_engagement`, `pages_manage_posts`.
5. Generate a long-lived token.

### 4. Repository secrets

`Settings → Secrets and variables → Actions`

| Secret | Needed for |
|---|---|
| `GEMINI_API_KEY` | script writing (free at aistudio.google.com) |
| `TELEGRAM_BOT_TOKEN` | approval messages |
| `TELEGRAM_CHAT_ID` | approval messages |
| `IG_USER_ID` | publishing |
| `IG_ACCESS_TOKEN` | publishing |
| `PEXELS_API_KEY` | optional, better imagery |
| `PIXABAY_API_KEY` | optional |
| `FB_APP_ID`, `FB_APP_SECRET` | optional, token auto-refresh |
| `ADMIN_PAT` | optional, PAT with `secrets: write` for auto-refresh |

Optional variables: `SCRIPT_PROVIDER`, `TTS_VOICE`.

### 5. Turn it on

Run **Build reel** manually once from the Actions tab. If a reel lands in
Telegram with working buttons, the whole chain is wired.

---

## Local use

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt install ffmpeg fonts-dejavu      # or: brew install ffmpeg

cp .env.example .env    # add a free Gemini or Groq key
python run.py build
```

Output in `out/<slug>/`: `reel.mp4`, `script.txt`, `caption.txt`,
`captions.srt`, plus the downloaded shot images.

```bash
python run.py list
python run.py approve <slug>
python run.py reject <slug>
```

---

## Timing and limits

- GitHub's minimum cron is 5 minutes, and scheduled runs get delayed under
  load. A few minutes between tapping Approve and the post appearing is
  normal; occasionally longer.
- GitHub Actions is free and unlimited on public repos. On private repos
  the 5-minute poll alone burns roughly 8,600 minutes/month against a
  2,000-minute free tier — **use a public repo, or widen the poll to
  `*/30`.**
- Telegram bots can't upload files over 50 MB. Reels come in around 10–20 MB,
  so this only bites if you raise the resolution a lot.
- Instagram allows ~50 published posts per rolling 24h. One a day is fine.

---

## Content rules built in

**Copyright.** ScienceDaily's text and the underlying university press
releases are copyrighted. The script step re-explains findings from
scratch, and `core/script.py` rejects any draft sharing more than 12% of
its 4-word sequences with the source, retrying with feedback. Captions
credit ScienceDaily and link the article.

**Accuracy.** Retention writing and research accuracy pull against each
other; the usual failure is a hook promising more than the study found.
Four validators run on every draft:

- banned hype phrases (`changes everything`, `scientists are baffled`, …)
- overclaim patterns (`proves that`, `cures`, `will eliminate`, …)
- loop closure — the hook's promise must be answered in the findings
- section completeness — a script can't drop the method or the caveats to
  buy room for the dramatic parts

Failures regenerate with specific feedback, up to four attempts, then fall
back to a visibly plain template.

**The approval gate.** An LLM summarising research unsupervised will
eventually overstate something, and it posts under your name. One tap is
cheap insurance.

---

## Troubleshooting

**Buttons do nothing** — check `poll.yml` is enabled (GitHub disables
scheduled workflows after 60 days of repo inactivity), and that no webhook
is set on the bot: `curl .../getWebhookInfo` should show an empty url.

**`error 9007`** — the container wasn't `FINISHED`. Usually means the video
URL isn't publicly reachable; a private repo's release assets won't work.

**Lots of blank dark cards** — the script's shot queries were too abstract.
Check `script.txt`, and consider a stronger model.

**Captions out of sync** — timings come from edge-tts word boundaries, so
drift means audio was re-encoded after the ASS was built. Rebuild both.

**Token expired** — set up `refresh-token.yml`, or regenerate manually every
60 days.


---

## Topics and keywords

Stories are discovered two ways: the RSS feeds listed per topic, and a
Google News keyword search that reaches essentially every publisher.

```bash
python run.py keywords list
python run.py keywords preview                      # what matches right now
python run.py keywords add --topic conflict "trump iran" "drone strike"
python run.py keywords remove --topic tech "chip"
python run.py keywords enable --topic markets
python run.py keywords disable --topic crime
```

Or edit `keywords.json` directly.

### Tiers

Each topic has a tier that controls how it is handled:

| Tier | Topics | Waits | Auto-post | Extra rules |
|---|---|---|---|---|
| `safe` | science, tech | 0h | allowed | normal |
| `caution` | health | 12h | never | must name study stage and what isn't shown |
| `sensitive` | conflict, markets, crime | 24h | never | attribution required, no casualties, no forecasts |

By default only `safe` topics are considered. To include others:

```bash
python run.py build --max-tier sensitive
```

### Why sensitive topics wait a day

Breaking news is wrong most often in its first hours — casualty counts,
attribution, "sources say" claims that get retracted. Waiting until the
next morning means the corrections have landed, and the follow-up coverage
is longer, which also gives the script real material instead of a headline.

This is a feature, not a limitation. A channel that is reliably right about
yesterday beats one that is sometimes wrong about today.

### The thin-source rule

A story needs `MIN_SOURCE_WORDS` (120) of recoverable article text or it is
skipped. Google News gives a headline and a sentence, then links to a
publisher that may paywall or block us. An 85-second script needs ~230
words — hand the model 40 and it invents the rest. Skipping is correct.

Expect many skips when running broad keywords. The log says why.

### Publisher filtering

This matters more than keyword tuning. A bare keyword search across the open
web returns horoscopes for "breakthrough" and sports previews for "first
time". `allowed_domains` restricts results to known publishers and removes
almost all of it in one step; `blocked_domains` catches the rest.

Google News names the real publisher in its feed, so filtering works even
though the links go through Google's domain.

Two further guards: single-word keywords only count if they appear in the
**title** (otherwise "chip" matches a writer named Chip), and each topic has
a `search_context` appended to its queries, so "breakthrough" is searched as
"breakthrough research study scientists journal".

If a story you wanted is missing, check whether its publisher is in
`allowed_domains` — that is the most likely reason.

### Blocked terms

`blocked_terms` in `keywords.json` rejects a story outright, matched with
inflections — "killed" also catches "kills" and "killing". The defaults
cover deaths, shootings and abuse. These are not subjects an automated
pipeline should narrate.

---

## Honest limits on general news

This pipeline was designed for science press releases, which are published
to be summarised and don't change after publication. Broadening it to all
news brings real risks:

**Copyright.** University press releases are meant to be reproduced. Wire
copy from Reuters, AP and Bloomberg is commercially protected and
aggressively enforced. The 12% overlap guard still runs, but rewriting wire
copy at scale into monetised video is a different legal posture from
rewriting a university release. Prefer publishers' own feeds over wires.

**Accuracy.** Validators catch hype and overclaim. They cannot catch a
false premise in the source, and they cannot know a story was retracted an
hour after you built the reel. The 24h delay and the approval gate are what
actually protect you here.

**Platform risk.** Automated accounts posting sensationalised conflict
content get actioned by Instagram. Keeping the account mostly science and
tech, with occasional slower analysis pieces, is both safer and generally
performs better than chasing breaking news you'll always lose to real
newsrooms.

**Your name.** You are a professor. A single confidently wrong video about
a live conflict is a different kind of cost than a flat one about a frog.
Weigh that before enabling the sensitive tiers.
