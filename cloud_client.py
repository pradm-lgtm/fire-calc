#!/usr/bin/env python3
"""
The submitter's view of a hosted approval page.

Presents the same handful of operations as the local database, so the
submitter does not care whether proposals live in a file next to it or on a
host somewhere. Everything is an outbound HTTPS request, which is what lets
the Mac sit behind any router with no VPN, no open ports and no fixed
address.

Configured by environment:
    FANTASY_API_URL     https://your-app.fly.dev
    FANTASY_API_TOKEN   the same token the page was deployed with
"""

import json
import os
import urllib.error
import urllib.request


class RemoteError(RuntimeError):
    pass


def configured():
    return bool(os.environ.get("FANTASY_API_URL"))


def base_url():
    return (os.environ.get("FANTASY_API_URL") or "").rstrip("/")


def _call(path, payload=None, timeout=30):
    url = base_url() + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data)
    req.add_header("Authorization",
                   "Bearer " + os.environ.get("FANTASY_API_TOKEN", ""))
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        if e.code == 401:
            # The host says which side is stale; repeating that beats a
            # generic "check your token", which is where three rounds went.
            try:
                said = json.loads(detail).get("error", detail)
            except json.JSONDecodeError:
                said = detail
            raise RemoteError(f"the host rejected the API token: {said}")
        raise RemoteError(f"HTTP {e.code} from {url}: {detail}")
    except urllib.error.URLError as e:
        raise RemoteError(f"could not reach {url}: {e.reason}")
    try:
        return json.loads(body or "{}")
    except json.JSONDecodeError:
        raise RemoteError(f"non-JSON reply from {url}: {body[:120]}")


def approved_unsubmitted():
    """Approved claims awaiting submission, newest run first."""
    return _call("/api/claims").get("claims", [])


def push_proposals(season, week, sources, proposals):
    """Send a week's proposals to the host that serves the approval page."""
    return _call("/api/proposals", {
        "season": season, "week": week, "sources": sources,
        "proposals": proposals,
    }, timeout=120)


def push_lineup(season, week, sources, flags):
    """Send this week's start/sit verdicts to the page."""
    return _call("/api/lineup", {
        "season": season, "week": week, "sources": sources, "flags": flags,
    }, timeout=120)


def recent_claims():
    """Approved and recently submitted claims, for auditing."""
    return _call("/api/claims?include=submitted").get("claims", [])


def report(proposal_id, submitted, ok=False, detail=""):
    """Tell the host what happened.

    submitted=False means nothing reached the league, so the claim stays
    approved and can be tried again. Only a claim that actually reached the
    submit button is reported as submitted.
    """
    return _call(f"/api/claims/{int(proposal_id)}/result",
                 {"submitted": bool(submitted), "ok": bool(ok),
                  "detail": str(detail)[:500]})
