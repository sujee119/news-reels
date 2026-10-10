"""Host the finished MP4 somewhere public, then publish it to Instagram.

Instagram's Graph API does not accept a file upload for Reels -- it fetches
the video from a URL you give it. So the video must be publicly reachable
for the duration of the publish. Three strategies are supported:

  r2      Cloudflare R2 (free tier, S3-compatible, needs boto3)
  github  attach to a GitHub release via the `gh` CLI
  manual  print the path and wait for you to supply a URL

The publish itself is the documented three-step flow:
  1. create a media container (media_type=REELS, video_url=...)
  2. poll the container until status_code == FINISHED
  3. publish the container

Skipping step 2 is what produces error 9007 in production.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import config

UA = "scidaily-reels/1.0"
GRAPH = "https://graph.facebook.com"


# ------------------------------------------------------------------ hosting

def _host_r2(path: Path) -> str:
    import boto3  # lazy: only needed for this strategy

    if not all([config.R2_ACCOUNT_ID, config.R2_ACCESS_KEY,
                config.R2_SECRET_KEY, config.R2_BUCKET, config.R2_PUBLIC_BASE]):
        raise RuntimeError("R2 selected but R2_* settings are incomplete")

    client = boto3.client(
        "s3",
        endpoint_url=f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=config.R2_ACCESS_KEY,
        aws_secret_access_key=config.R2_SECRET_KEY,
        region_name="auto",
    )
    key = f"reels/{path.name}"
    client.upload_file(str(path), config.R2_BUCKET, key,
                       ExtraArgs={"ContentType": "video/mp4"})
    return f"{config.R2_PUBLIC_BASE.rstrip('/')}/{key}"


def _host_github(path: Path) -> str:
    import subprocess
    tag = f"reel-{path.stem}"
    subprocess.run(["gh", "release", "create", tag, str(path),
                    "--title", tag, "--notes", "auto-generated reel"],
                   check=True, capture_output=True)
    out = subprocess.run(["gh", "release", "view", tag, "--json", "assets"],
                         check=True, capture_output=True, text=True)
    assets = json.loads(out.stdout)["assets"]
    return assets[0]["url"]


def host(path: Path) -> str:
    provider = config.HOST_PROVIDER
    if provider == "r2":
        return _host_r2(path)
    if provider == "github":
        return _host_github(path)
    raise RuntimeError(
        f"HOST_PROVIDER is '{provider}'. The file is ready at {path} but no "
        f"automatic host is configured -- Instagram needs a public URL. "
        f"Set HOST_PROVIDER=r2 or github, or upload it yourself and pass "
        f"--video-url.")


# ---------------------------------------------------------------- instagram

def _graph_post(endpoint: str, params: dict, timeout: int = 60) -> dict:
    url = f"{GRAPH}/{config.IG_GRAPH_VERSION}/{endpoint}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Graph API {endpoint}: {exc.read().decode()[:500]}")


def _graph_get(endpoint: str, params: dict, timeout: int = 60) -> dict:
    url = (f"{GRAPH}/{config.IG_GRAPH_VERSION}/{endpoint}?"
           + urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Graph API {endpoint}: {exc.read().decode()[:500]}")


def publish_reel(video_url: str, caption: str,
                 poll_seconds: int = 8, max_wait: int = 600) -> str:
    """Run the three-step publish. Returns the published media id."""
    if not (config.IG_USER_ID and config.IG_ACCESS_TOKEN):
        raise RuntimeError("IG_USER_ID and IG_ACCESS_TOKEN must be set")

    print("  -> creating media container")
    container = _graph_post(f"{config.IG_USER_ID}/media", {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": config.IG_ACCESS_TOKEN,
    })
    cid = container["id"]
    print(f"     container {cid}")

    print("  -> waiting for Instagram to finish processing")
    waited = 0
    while waited < max_wait:
        time.sleep(poll_seconds)
        waited += poll_seconds
        status = _graph_get(cid, {
            "fields": "status_code,status",
            "access_token": config.IG_ACCESS_TOKEN,
        })
        code = status.get("status_code")
        if code == "FINISHED":
            print(f"     ready after {waited}s")
            break
        if code == "ERROR":
            raise RuntimeError(f"processing failed: {status.get('status')}")
        print(f"     {code} ({waited}s)")
    else:
        raise RuntimeError(f"container not ready after {max_wait}s")

    print("  -> publishing")
    result = _graph_post(f"{config.IG_USER_ID}/media_publish", {
        "creation_id": cid,
        "access_token": config.IG_ACCESS_TOKEN,
    })
    return result["id"]


def remaining_quota() -> int | None:
    """Instagram allows ~50 published posts per rolling 24h."""
    try:
        data = _graph_get(f"{config.IG_USER_ID}/content_publishing_limit", {
            "fields": "config,quota_usage",
            "access_token": config.IG_ACCESS_TOKEN,
        })
        row = data["data"][0]
        return row.get("config", {}).get("quota_total", 50) - row.get("quota_usage", 0)
    except Exception:
        return None


# ------------------------------------------------------------- notification

def notify(text: str, video_path: Path | None = None) -> None:
    """Optional Telegram ping for the approval step. Silent if unconfigured."""
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    try:
        base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
        data = urllib.parse.urlencode({
            "chat_id": config.TELEGRAM_CHAT_ID, "text": text}).encode()
        urllib.request.urlopen(
            urllib.request.Request(f"{base}/sendMessage", data=data), timeout=30)
    except Exception as exc:
        print(f"  ! notify failed: {exc}")
