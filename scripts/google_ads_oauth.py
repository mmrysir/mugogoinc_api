"""Run a Google Ads OAuth authorization-code flow and print a fresh refresh token.

Uses the Google Ads **Desktop** OAuth client credentials (GOOGLE_ADS_CLIENT_ID /
GOOGLE_ADS_CLIENT_SECRET) and the ``https://www.googleapis.com/auth/adwords``
scope. Works for "Installed" / Desktop type OAuth clients; the refresh token is
long-lived and is what the production backend consumes via the
``GOOGLE_ADS_REFRESH_TOKEN`` environment variable (GitHub Secret → server .env).

Usage:

    # 1. Point the Desktop client at the redirect URI
    #    https://developers.google.com/console → OAuth client → Authorized redirect URIs
    #      → http://localhost:8765/

    # 2. Run the flow (opens a local browser callback server on port 8765)
    PYTHONPATH=. GOOGLE_ADS_CLIENT_ID=... GOOGLE_ADS_CLIENT_SECRET=... python scripts/google_ads_oauth.py

    # 3. Copy the printed refresh token into the GOOGLE_ADS_REFRESH_TOKEN
    #    GitHub Secret (gh secret set GOOGLE_ADS_REFRESH_TOKEN "...") and redeploy.

    # Manual code-exchange fallback (no local server):
    PYTHONPATH=. python scripts/google_ads_oauth.py --manual

Exit code is 0 only when a refresh token was obtained and validated.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse, urlencode
from urllib.request import urlopen

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings  # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/adwords"
REDIRECT_URI = "http://localhost:8765/"
PORT = 8765


def _token_endpoint(client_id: str, client_secret: str, refresh_token: str) -> dict:
    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15.0,
    )
    return {"status_code": resp.status_code, "body": resp.json()}


def _run_local_callback_server(code_box: dict) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            code_box["code"] = qs.get("code", [None])[0]
            code_box["error"] = qs.get("error", [None])[0]
            body = b"<html><body><h2>You can close this window now.</h2></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()


def main() -> int:
    client_id = settings.google_ads_client_id
    client_secret = settings.google_ads_client_secret
    if not client_id or not client_secret:
        print("ERROR: GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET are not configured.")
        return 1

    auth_params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{AUTH_URL}?{urlencode(auth_params)}"

    manual = "--manual" in sys.argv
    code_box: dict = {}

    if not manual:
        _run_local_callback_server(code_box)
        print("Opening your browser for Google Ads authorization...")
        print(auth_url)
        print("Waiting for authorization on http://localhost:8765/ ...")
        deadline = time.time() + 300
        while time.time() < deadline:
            if code_box.get("code") or code_box.get("error"):
                break
            time.sleep(1)
    else:
        code = input("Paste the authorization code from Google: ").strip()
        code_box["code"] = code

    if code_box.get("error"):
        print(f"ERROR: Google returned error: {code_box['error']}")
        return 1
    if not code_box.get("code"):
        print("ERROR: No authorization code received within the timeout.")
        return 1

    token_resp = httpx.post(
        TOKEN_URL,
        data={
            "code": code_box["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=15.0,
    )
    token_resp.raise_for_status()
    token_data = token_resp.json()
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print("ERROR: Google did not return a refresh_token. Check the OAuth client type.")
        return 1

    check = _token_endpoint(client_id, client_secret, refresh_token)
    if check["status_code"] != 200:
        print(f"ERROR: Refresh token validation failed: {json.dumps(check['body'])}")
        return 1

    print("\n=== SUCCESS ===")
    print(f"Refresh token obtained and validated: {refresh_token}")
    print("Add it to the server .env as GOOGLE_ADS_REFRESH_TOKEN and to GitHub")
    print("Secrets as GOOGLE_ADS_REFRESH_TOKEN, then re-deploy.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
