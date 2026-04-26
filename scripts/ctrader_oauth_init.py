#!/usr/bin/env python3
"""
scripts/ctrader_oauth_init.py — One-time OAuth2 authorization for cTrader Open API.

Run this ONCE interactively to authorize the FluxQuantumAI application against
your cTrader account. It will:
  1. Print an authorization URL
  2. Open it in the default browser (if available)
  3. Wait for you to paste back the authorization code (?code=... param)
  4. Exchange the code for accessToken + refreshToken
  5. Append both to C:\\FluxQuantumAI\\.env

After this, ctrader_executor.py can connect autonomously and the executor
will auto-refresh the access_token using the refresh_token when it expires.

Prereqs in .env:
  CTRADER_CLIENT_ID
  CTRADER_CLIENT_SECRET

Usage:
  python scripts/ctrader_oauth_init.py
  # follow on-screen instructions

Note: the redirect URI must be registered in your cTrader application
config at https://openapi.ctrader.com/apps. We default to
http://localhost/ — change with --redirect-uri if you registered a
different one.
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _persist_env(path: Path, key: str, value: str) -> None:
    """Idempotent set of one .env key=value line."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    found = False
    out = []
    for ln in lines:
        if ln.startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="cTrader OAuth2 one-time init")
    parser.add_argument("--redirect-uri", default="http://localhost/",
                        help="Must match what you registered in your cTrader application config "
                             "(default: http://localhost/)")
    parser.add_argument("--scope", default="trading", choices=["trading", "accounts"])
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not auto-open the URL in a browser")
    parser.add_argument("--env-path", default=str(REPO_ROOT / ".env"),
                        help="Path to .env file (default: ./.env)")
    args = parser.parse_args()

    env_path = Path(args.env_path)
    _load_env(env_path)

    client_id = os.environ.get("CTRADER_CLIENT_ID", "").strip()
    client_secret = os.environ.get("CTRADER_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        print("ERROR: CTRADER_CLIENT_ID and/or CTRADER_CLIENT_SECRET missing from .env",
              file=sys.stderr)
        print("Add them from C:\\FluxQuantum_NextGen_RB\\NextGen_RB_Docs\\FluxQuantumAI Application cTrader.txt",
              file=sys.stderr)
        return 2

    try:
        from ctrader_open_api.auth import Auth
    except ImportError as e:
        print(f"ERROR: ctrader-open-api not installed in this Python: {e}", file=sys.stderr)
        return 3

    auth = Auth(client_id, client_secret, args.redirect_uri)
    auth_url = auth.getAuthUri(scope=args.scope)

    print("\n" + "=" * 72)
    print("STEP 1 — Open this URL in a browser and authorize:")
    print("=" * 72)
    print(auth_url)
    print("=" * 72)
    if not args.no_browser:
        try:
            webbrowser.open(auth_url, new=2)
        except Exception:
            pass

    print()
    print("STEP 2 — After authorizing, you'll be redirected to a URL that LOOKS LIKE:")
    print(f"   {args.redirect_uri}?code=ABCDEFG12345...")
    print()
    print("Paste the FULL redirect URL here (or just the code):")
    raw = input("> ").strip()
    if not raw:
        print("ERROR: empty input", file=sys.stderr)
        return 4

    if "code=" in raw:
        # Parse out the code parameter
        if "://" in raw:
            qs = parse_qs(urlparse(raw).query)
            code = (qs.get("code") or [""])[0]
        else:
            # raw is "code=XYZ" or "?code=XYZ"
            code = raw.split("code=", 1)[1].split("&", 1)[0]
    else:
        code = raw

    if not code:
        print("ERROR: could not extract code from input", file=sys.stderr)
        return 5

    print(f"\nSTEP 3 — Exchanging code for tokens (code length={len(code)})...")
    try:
        tok = auth.getToken(code)
    except Exception as e:
        print(f"ERROR: token exchange failed: {e}", file=sys.stderr)
        return 6

    access_token = tok.get("accessToken") or tok.get("access_token")
    refresh_token = tok.get("refreshToken") or tok.get("refresh_token")
    if not access_token:
        print(f"ERROR: response missing access token. Response keys: {list(tok.keys())}",
              file=sys.stderr)
        return 7

    _persist_env(env_path, "CTRADER_ACCESS_TOKEN", access_token)
    if refresh_token:
        _persist_env(env_path, "CTRADER_REFRESH_TOKEN", refresh_token)

    print("\nSUCCESS — tokens persisted to:", env_path)
    print(f"   CTRADER_ACCESS_TOKEN  : {len(access_token)} chars")
    print(f"   CTRADER_REFRESH_TOKEN : {len(refresh_token) if refresh_token else 0} chars")
    print("\nNext: confirm CTRADER_ACCOUNT_MODE in .env is 'demo' (default) or 'live',")
    print("then ctrader_executor.py can connect autonomously.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
