#!/usr/bin/env python3
"""One-time: turn a Desktop OAuth client into a refresh token for ONE channel.

Run it once per channel, signed in as that channel. It prints a refresh
token; that goes into the repo's YT_REFRESH_TOKEN secret and the pipeline
uses it forever.

    python tools/youtube_auth.py

Before running, the consent screen's publishing status must be "In
production". While it is "Testing", Google expires refresh tokens after
seven days and uploads would stop every week with no obvious cause.

Nothing is written to disk and nothing is sent anywhere except Google.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from getpass import getpass

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
# The out-of-band flow for desktop clients: Google shows the code on screen
# instead of redirecting, so no local web server is needed.
REDIRECT = "urn:ietf:wg:oauth:2.0:oob"


def post(url: str, fields: dict) -> dict:
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"\nGoogle refused it:\n{exc.read().decode()[:600]}\n")


def main() -> None:
    print(__doc__)
    client_id = input("Client ID: ").strip()
    client_secret = getpass("Client secret (hidden, paste and press Enter): ").strip()
    if not client_id or not client_secret:
        sys.exit("both are required")

    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        # Without this, a second authorisation returns no refresh token at
        # all, which is the usual reason the second channel "doesn't work".
        "prompt": "consent",
    })

    print("\n" + "=" * 68)
    print("1. Open this URL in a browser where you are signed in as the")
    print("   CHANNEL you want to upload to (use the account switcher):\n")
    print(url)
    print("\n2. Click through the 'Google hasn't verified this app' warning")
    print("   -- Advanced, then 'Go to reel uploader (unsafe)'. That warning")
    print("   is expected for your own unverified app.")
    print("3. Approve, then copy the code Google shows you.")
    print("=" * 68 + "\n")

    code = input("Paste the code: ").strip()
    if not code:
        sys.exit("no code given")

    data = post(TOKEN, {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT,
    })
    refresh = data.get("refresh_token")
    if not refresh:
        sys.exit("No refresh_token came back. That happens when the account "
                 "has authorised this app before -- revoke it at "
                 "https://myaccount.google.com/permissions and run again.")

    print("\nRefresh token (store it, do not paste it into a chat):\n")
    print(refresh)
    print("\nSet it on the right repo with:\n")
    print("    gh secret set YT_REFRESH_TOKEN\n")


if __name__ == "__main__":
    main()
