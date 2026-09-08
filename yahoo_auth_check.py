#!/usr/bin/env python3
"""
Yahoo Fantasy Sports auth + scope checker (step 1 of the fantasy agent).

Checks, in order:
  1. Loads YAHOO_* credentials from a local .env file (never hardcoded).
  2. Refreshes the OAuth2 access token via Yahoo's token endpoint.
  3. Makes one read call (your NFL leagues) to confirm auth works.
  4. Checks write scope two ways:
       a. Inspects the `scope` field of the token response, if Yahoo returns
          one (fspt-w = read/write, fspt-r = read-only).
       b. Re-submits your current lineup UNCHANGED via PUT /team/.../roster —
          a no-op write. Success proves read/write; a scope/permission
          rejection proves read-only.

Exit codes: 0 = read/write confirmed, 1 = unexpected error,
            2 = refresh token dead (full re-auth needed), 3 = read-only.

First-time setup (no access/refresh token yet): fill in only
YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET in .env, then run

    python3 yahoo_auth_check.py --auth-url        # prints the consent URL
    python3 yahoo_auth_check.py --exchange CODE   # trades the code for tokens,
                                                  # saves them to .env, runs check

Stdlib only — no pip installs needed. Requires Python 3.8+.
"""

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

REQUIRED_VARS = [
    "YAHOO_CLIENT_ID",
    "YAHOO_CLIENT_SECRET",
    "YAHOO_ACCESS_TOKEN",
    "YAHOO_REFRESH_TOKEN",
]


# ---------------------------------------------------------------- .env

def load_env(path: Path) -> dict:
    """Minimal .env parser: KEY=VALUE lines, '#' comments, optional quotes."""
    env = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("'\"")
        env[key.strip()] = value
    return env


# ---------------------------------------------------------------- HTTP

def http_request(url, method="GET", headers=None, body=None):
    """Return (status_code, body_text). Never raises on HTTP error status."""
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def api_get(path, access_token):
    return http_request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
    )


def save_env(path: Path, env: dict):
    """Rewrite the .env file with the four YAHOO_* keys (preserving others)."""
    lines, seen = [], set()
    if path.exists():
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.partition("=")[0].strip()
                if key in env:
                    lines.append(f"{key}={env[key]}")
                    seen.add(key)
                    continue
            lines.append(line)
    for key in REQUIRED_VARS:
        if key in env and key not in seen:
            lines.append(f"{key}={env[key]}")
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------- OAuth

def token_request(env, params):
    basic = base64.b64encode(
        f"{env['YAHOO_CLIENT_ID']}:{env['YAHOO_CLIENT_SECRET']}".encode()
    ).decode()
    status, text = http_request(
        TOKEN_URL,
        method="POST",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        body=urllib.parse.urlencode(params).encode(),
    )
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {}
    return status, text, data


def exchange_code(env, code, env_path):
    """One-time: trade a consent-screen code for access + refresh tokens."""
    status, text, data = token_request(env, {
        "grant_type": "authorization_code",
        "code": code.strip(),
        "redirect_uri": "oob",
    })
    if status != 200 or "access_token" not in data:
        print(f"Code exchange FAILED (HTTP {status}):")
        print(text)
        print()
        print("Codes are single-use and expire after a few minutes — get a fresh")
        print("one with:  python3 yahoo_auth_check.py --auth-url")
        return False
    env["YAHOO_ACCESS_TOKEN"] = data["access_token"]
    env["YAHOO_REFRESH_TOKEN"] = data["refresh_token"]
    save_env(env_path, env)
    print(f"Code exchange OK — tokens saved to {env_path}")
    if data.get("scope"):
        print(f"Token response scope: {data['scope']!r}")
    return True


def refresh_access_token(env):
    return token_request(env, {
        "grant_type": "refresh_token",
        "refresh_token": env["YAHOO_REFRESH_TOKEN"],
        "redirect_uri": "oob",
    })


# ---------------------------------------------------------------- Yahoo JSON helpers
# Yahoo's fantasy JSON is deeply nested lists-of-single-key-dicts; these two
# walkers avoid hand-coding every level of that structure.

def deep_find(obj, key):
    """Yield every value stored under `key`, anywhere in the structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from deep_find(v, key)
    elif isinstance(obj, list):
        for item in obj:
            yield from deep_find(item, key)


def flatten_scalars(obj):
    """Collect first-seen scalar values by key from a nested fragment."""
    out = {}

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                else:
                    out.setdefault(k, v)
        elif isinstance(o, list):
            for item in o:
                walk(item)

    walk(obj)
    return out


# ---------------------------------------------------------------- Write probe

def build_roster_xml(coverage_type, coverage_value, players):
    lines = [
        '<?xml version="1.0"?>',
        "<fantasy_content>",
        "  <roster>",
        f"    <coverage_type>{coverage_type}</coverage_type>",
        f"    <{coverage_type}>{coverage_value}</{coverage_type}>",
        "    <players>",
    ]
    for player_key, position in players:
        lines += [
            "      <player>",
            f"        <player_key>{player_key}</player_key>",
            f"        <position>{position}</position>",
            "      </player>",
        ]
    lines += ["    </players>", "  </roster>", "</fantasy_content>"]
    return "\n".join(lines)


def looks_like_scope_denial(status, body):
    if status not in (401, 403):
        return False
    lowered = body.lower()
    return any(
        marker in lowered
        for marker in ("scope", "not allowed", "permission", "forbidden",
                       "insufficient", "read-only", "fspt-r")
    ) or status == 403


# ---------------------------------------------------------------- Main

def main():
    env_path = Path(__file__).resolve().parent / ".env"
    env = load_env(env_path)

    args = sys.argv[1:]
    needs_tokens = True
    if args[:1] == ["--auth-url"] or args[:1] == ["--exchange"]:
        needs_tokens = False
    required = REQUIRED_VARS if needs_tokens else REQUIRED_VARS[:2]
    missing = [v for v in required if not env.get(v)]
    if missing:
        print(f"ERROR: missing in {env_path}: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in your Yahoo app credentials.")
        return 1

    if args[:1] == ["--auth-url"]:
        print("Open this URL in a browser, allow access, and copy the code shown:")
        print(f"{AUTH_URL}?" + urllib.parse.urlencode({
            "client_id": env["YAHOO_CLIENT_ID"],
            "redirect_uri": "oob",
            "response_type": "code",
        }))
        print()
        print("Then run:  python3 yahoo_auth_check.py --exchange <code>")
        return 0

    if args[:1] == ["--exchange"]:
        if len(args) < 2:
            print("Usage: python3 yahoo_auth_check.py --exchange <code>")
            return 1
        if not exchange_code(env, args[1], env_path):
            return 2
        env = load_env(env_path)
        print()
        # fall through to the normal check, exercising the new refresh token
    elif args:
        print(f"Unknown arguments: {' '.join(args)}")
        print("Usage: python3 yahoo_auth_check.py [--auth-url | --exchange <code>]")
        return 1

    # ---- Step 1: refresh the access token -------------------------------
    print("=" * 70)
    print("STEP 1: Refreshing OAuth2 access token")
    print("=" * 70)
    status, raw, tok = refresh_access_token(env)

    if status != 200:
        err = tok.get("error", "")
        desc = tok.get("error_description", "")
        if err == "invalid_grant" or "invalid_grant" in raw.lower() or (
            status in (400, 401) and "expired" in raw.lower()
        ):
            print(f"HTTP {status}: {err} — {desc or raw}")
            print()
            print("RESULT: REFRESH TOKEN INVALID/EXPIRED — full re-auth needed.")
            print("You'll have to redo the browser authorization flow to get a")
            print("new refresh token before anything else can be tested.")
            return 2
        print(f"Unexpected token endpoint response (HTTP {status}):")
        print(raw)
        return 1

    access_token = tok.get("access_token")
    if not access_token:
        print(f"HTTP 200 but no access_token in response:\n{raw}")
        return 1

    print("OK: token refreshed.")
    if tok.get("refresh_token") and tok["refresh_token"] != env["YAHOO_REFRESH_TOKEN"]:
        print("NOTE: Yahoo issued a NEW refresh token — update YAHOO_REFRESH_TOKEN")
        print(f"      in .env to: {tok['refresh_token']}")

    token_scope = tok.get("scope") or tok.get("xoauth_yahoo_guid_scope") or ""
    scope_says_write = None  # None = token response didn't say
    if token_scope:
        print(f"Token response scope: {token_scope!r}")
        if "fspt-w" in token_scope:
            scope_says_write = True
            print("  -> 'fspt-w' present: Fantasy Sports READ/WRITE scope.")
        elif "fspt-r" in token_scope:
            scope_says_write = False
            print("  -> 'fspt-r' present (no fspt-w): Fantasy Sports READ-ONLY scope.")
    else:
        print("Token response did not include a scope field (common for Yahoo);")
        print("will rely on the no-op lineup write probe below.")

    # ---- Step 2: read check ---------------------------------------------
    print()
    print("=" * 70)
    print("STEP 2: Read check — fetching your NFL leagues")
    print("=" * 70)
    status, body = api_get(
        "/users;use_login=1/games;game_keys=nfl/leagues?format=json", access_token
    )
    if status != 200:
        print(f"Read call FAILED (HTTP {status}). Raw response:")
        print(body)
        print()
        print("RESULT: token refreshed but the read call failed — see raw error above.")
        return 1

    leagues_json = json.loads(body)
    leagues = []
    for league in deep_find(leagues_json, "league"):
        frag = flatten_scalars(league)
        if frag.get("league_key"):
            leagues.append((frag["league_key"], frag.get("name", "?")))
    leagues = list(dict.fromkeys(leagues))
    print(f"OK: read access confirmed. Found {len(leagues)} NFL league(s):")
    for key, name in leagues:
        print(f"  - {name}  ({key})")
    if not leagues:
        print("  (none — is this the right Yahoo account / is the season active?)")

    # ---- Step 3: write-scope probe (no-op lineup resubmit) ---------------
    print()
    print("=" * 70)
    print("STEP 3: Write-scope probe — re-submitting current lineup UNCHANGED")
    print("=" * 70)

    status, body = api_get(
        "/users;use_login=1/games;game_keys=nfl/teams?format=json", access_token
    )
    if status != 200:
        print(f"Could not fetch your teams (HTTP {status}). Raw response:")
        print(body)
        return 1
    team_keys = list(dict.fromkeys(
        v for v in deep_find(json.loads(body), "team_key") if isinstance(v, str)
    ))
    if not team_keys:
        print("No NFL teams found for this account; cannot run the write probe.")
        print("(Scope from token response above is the only signal available.)")
        return 0 if scope_says_write else 1
    team_key = team_keys[0]
    print(f"Using team: {team_key}")

    status, body = api_get(f"/team/{team_key}/roster?format=json", access_token)
    if status != 200:
        print(f"Could not fetch roster (HTTP {status}). Raw response:")
        print(body)
        return 1
    roster_json = json.loads(body)
    coverage_type = next(deep_find(roster_json, "coverage_type"), "week")
    coverage_value = next(deep_find(roster_json, coverage_type), None)
    players = []
    for player in deep_find(roster_json, "player"):
        pk = flatten_scalars(player).get("player_key")
        # Position must come from selected_position only — the player fragment
        # also carries eligible_positions, and grabbing one of those would turn
        # this "no-op" resubmit into a real lineup change.
        sel = next(deep_find(player, "selected_position"), None)
        pos = flatten_scalars(sel).get("position") if sel is not None else None
        if pk and pos:
            players.append((pk, pos))
    players = list(dict.fromkeys(players))
    if not players or coverage_value is None:
        print("Could not parse roster into player/position pairs. Raw roster JSON:")
        print(json.dumps(roster_json, indent=2)[:4000])
        return 1
    print(f"Current roster ({coverage_type} {coverage_value}): "
          f"{len(players)} players. Re-submitting identical lineup (no-op)...")

    xml = build_roster_xml(coverage_type, coverage_value, players)
    status, body = http_request(
        f"{API_BASE}/team/{team_key}/roster",
        method="PUT",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/xml",
        },
        body=xml.encode(),
    )

    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    if 200 <= status < 300:
        print(f"No-op lineup PUT succeeded (HTTP {status}).")
        print()
        print("RESULT: READ/WRITE CONFIRMED — the token can submit lineup changes")
        print("(and by extension waiver claims) via the API. Build real Yahoo API")
        print("calls for roster moves; no browser automation needed for Yahoo.")
        return 0
    if looks_like_scope_denial(status, body):
        print(f"Write rejected with HTTP {status}. Raw response:")
        print(body)
        print()
        print("RESULT: READ-ONLY — refresh + reads work, but the token lacks write")
        print("scope (app was registered with Fantasy Sports 'Read'). Either")
        print("re-register/re-auth the Yahoo app with Read/Write, or plan on")
        print("browser automation for Yahoo just like Sleeper.")
        return 3
    print(f"Unexpected write-probe response (HTTP {status}). Raw response:")
    print(body)
    print()
    print("RESULT: INCONCLUSIVE — not a clean success or scope denial. Common")
    print("causes: locked players (games in progress), invalid position for the")
    print("coverage period, or rate limiting (Yahoo HTTP 999). Note that a")
    print("validation-type 400 error still implies WRITE scope (Yahoo parsed the")
    print("write request instead of rejecting it for permissions).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
