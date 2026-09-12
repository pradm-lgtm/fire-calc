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

import argparse
import base64
import hashlib
import hmac
import os
import pathlib
import secrets
import sys
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


def fingerprint(value):
    """Eight hex characters of a hash of a secret.

    Enough to tell two secrets apart without revealing either, which is the
    difference between "they do not match" and knowing which side is stale.
    """
    value = str(value or "")
    return hashlib.sha256(value.encode()).hexdigest()[:8] if value else "unset"


def token_complaint(header_value):
    """Why a token was rejected, in terms that are safe to send back."""
    given = str(header_value or "")
    if given.lower().startswith("bearer "):
        given = given[7:]
    if not given:
        return "no bearer token reached the server"
    if not api_token():
        return "the server has no FANTASY_API_TOKEN set"
    return (f"token mismatch: the server expects {fingerprint(api_token())}, "
            f"the caller sent {fingerprint(given)}")


def generate_token():
    return secrets.token_urlsafe(32)


def token_from_env_file(path=None):
    """The token in .env, creating and saving one if there is none.

    The Mac and the deployment have to agree on this string, and every way
    of getting it there by hand has now failed once: generated in one place
    and never written to the other, or piped from a file that did not have
    it, which sets an empty one and reads as a mismatch.
    """
    import localenv

    path = pathlib.Path(path) if path else localenv.DEFAULT
    try:
        existing = localenv.parse(path.read_text()).get("FANTASY_API_TOKEN")
    except OSError:
        existing = None
    if existing:
        return existing
    token = generate_token()
    with path.open("a") as handle:
        handle.write(f"\nFANTASY_API_TOKEN={token}\n")
    return token


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip())
    ap.add_argument("--token", action="store_true",
                    help="print the API token from .env, creating one there "
                         "if absent, so it can be piped to the host")
    args = ap.parse_args()
    if args.token:
        print(token_from_env_file(), end="")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
