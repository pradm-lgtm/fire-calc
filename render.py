#!/usr/bin/env python3
"""
Fetch a page the way a browser sees it.

Plain HTTP gets the HTML a server sends, which for a modern rankings page is
a shell with no players in it - the table is built by JavaScript after load.
This runs the page in a real browser and reads the text afterwards.

Used only as a fallback, because it is far slower than a plain fetch and
most pages do not need it.
"""

import re

# Ranked tables often live below the fold and load as you scroll.
SCROLL_STEPS = 6
SETTLE_MS = 1500


def available():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def _chromium_path():
    import glob
    for pattern in ("/opt/pw-browsers/**/chrome",
                    "/opt/pw-browsers/**/headless_shell"):
        hits = glob.glob(pattern, recursive=True)
        if hits:
            return hits[0]
    return None


def fetch_rendered(url, quiet=False):
    """Page text after JavaScript has run, or None."""
    if not available():
        if not quiet:
            print("    (needs playwright: pip3 install playwright "
                  "&& python3 -m playwright install chromium)")
        return None
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            kwargs = {"headless": True}
            exe = _chromium_path()
            if exe:
                kwargs["executable_path"] = exe
            browser = pw.chromium.launch(**kwargs)
            page = browser.new_page(
                viewport={"width": 1400, "height": 1000},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/122.0 Safari/537.36")
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(SETTLE_MS)
            # Rankings past the first screen are often not rendered until
            # scrolled to, so walk down the page before reading it.
            for _ in range(SCROLL_STEPS):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(400)
            text = page.evaluate("() => document.body.innerText")
            browser.close()
    except Exception as exc:
        if not quiet:
            print(f"    (could not render: {type(exc).__name__}: "
                  f"{str(exc).splitlines()[0][:100]})")
        return None
    return re.sub(r"[ \t]{2,}", " ", text or "")
