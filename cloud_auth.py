#!/usr/bin/env python3
"""
Authentication for the hosted approval page.

On your own wifi the page's security was "you have to be on my wifi". Hosted,
it is on the public internet and can spend real FAAB, so it needs a lock.

Two separate credentials, because they protect different things:

  FANTASY_PASSWORD   what you type once in a browser. Establishes a signed
                     session cookie so you are not retyping it every Tuesday.
  FANTASY_API_TOKEN  what the submitter on your Mac sends. A long random
                     string, never typed, and revocable on its own without
                     locking you out of the page.

Both are read from the environment. Neither is ever written to the database
or logged. Sessions are signed rather than stored, so a restart does not log
you out and there is no session table to leak.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time

COOKIE = "fantasy_session"
SESSION_DAYS = 30


def _secret():
    """Signing key. Ephemeral if unset, which logs everyone out on restart."""
    return (os.environ.get("FANTASY_SECRET")
            or os.environ.get("FANTASY_PASSWORD", "")
            or secrets.token_hex(32)).encode()


def password():
    return os.environ.get("FANTASY_PASSWORD", "")


def api_token():
    return os.environ.get("FANTASY_API_TOKEN", "")


def auth_required():
    """Unset password means local use; hosted deployments must set one."""
    return bool(password())


def _sign(payload):
    mac = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_session():
    expires = int(time.time()) + SESSION_DAYS * 86400
    payload = str(expires)
    return f"{payload}.{_sign(payload)}"


def valid_session(cookie_value):
    if not cookie_value or "." not in cookie_value:
        return False
    payload, _, sig = cookie_value.rpartition(".")
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    try:
        return int(payload) > time.time()
    except ValueError:
        return False


def check_password(given):
    expected = password()
    if not expected:
        return True
    # Constant time, so a wrong guess cannot be narrowed down by timing.
    return hmac.compare_digest(str(given or ""), expected)


def check_api_token(header_value):
    expected = api_token()
    if not expected:
        return not auth_required()
    given = str(header_value or "")
    if given.lower().startswith("bearer "):
        given = given[7:]
    return hmac.compare_digest(given, expected)


def generate_token():
    return secrets.token_urlsafe(32)
