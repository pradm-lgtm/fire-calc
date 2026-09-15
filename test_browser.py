#!/usr/bin/env python3
"""The page driven by a real browser.

    python3 test_browser.py

Everything else here tests what the server sends. This tests what a browser
does with it, which is a different question and the one that was wrong: the
card's controls live outside the form element and join it by id, and the
script that greys a button out during submission was deleting that button's
own name and value from the request. A form's data is built after the submit
handler returns, disabled controls are left out of it, and so Approve posted
no action at all and the page came back with the card untouched. Nothing but
a browser was ever going to catch that.

Skipped, not failed, where Playwright or Chromium are not installed - the
migration script runs every test file before it will commit, and a machine
without a browser should still be able to ship a change to the reasoning.
"""

import glob
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import store as st

HERE = os.path.dirname(os.path.abspath(__file__))


def bundled_chromium():
    """A Chromium sitting where this container puts them, or None.

    Only consulted when Playwright cannot find one of its own, so a machine
    with a normal Playwright install needs nothing from here.
    """
    found = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return found[-1] if found else None


def launch(pw):
    """A browser, or a skip. Never a failure: this file is run by the

    migration script before it will commit, and a change to the reasoning
    should still be shippable from a machine with no browser installed.
    """
    try:
        return pw.chromium.launch(args=["--no-sandbox"])
    except Exception:
        pass
    path = bundled_chromium()
    if not path:
        raise unittest.SkipTest("no Chromium to drive")
    return pw.chromium.launch(executable_path=path, args=["--no-sandbox"])


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def seed(path):
    conn = st.connect(path)
    run = st.start_run(conn, "2026", 3, ["ESPN"])
    options = [
        {"id": "d1", "name": "J.K. Dobbins (DEN RB)", "position": "RB",
         "why": "weakest of your 5 RBs, more than you can start"},
        {"id": "d2", "name": "MarShawn Lloyd (GB RB)", "position": "RB",
         "why": "2nd weakest of your 5 RBs, more than you can start"},
    ]
    pid = st.add_proposal(
        conn, run, league_id="1", league_name="LEHG",
        league_note="10-team, half-PPR",
        add_player_id="blk", add_player_name="Kaelon Black (SF RB)",
        add_position="RB", drop_player_id="d1",
        drop_player_name="J.K. Dobbins (DEN RB)", drop_position="RB",
        bid=4, max_bid=100, bid_low=4, bid_high=4, consensus=3,
        sources=["https://espn.com/x"], rationale="", quote="",
        drop_options=options)
    conn.close()
    return run, pid


class Browser(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright is not installed")
        cls.dir = tempfile.mkdtemp()
        cls.db = os.path.join(cls.dir, "t.db")
        cls.port = free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = subprocess.Popen(
            [sys.executable, "webapp.py", "--port", str(cls.port),
             "--db", cls.db],
            cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, FANTASY_PASSWORD=""))
        cls.pw = sync_playwright().start()
        try:
            cls.browser = launch(cls.pw)
        except unittest.SkipTest:
            cls.pw.stop()
            cls.server.terminate()
            raise

    @classmethod
    def tearDownClass(cls):
        for close in (getattr(cls, "browser", None), getattr(cls, "pw", None)):
            try:
                close.close() if close is cls.browser else close.stop()
            except Exception:
                pass
        if getattr(cls, "server", None):
            cls.server.terminate()
            cls.server.wait(timeout=10)

    def setUp(self):
        # Emptied rather than deleted: the schema is created once per path,
        # so removing the file leaves the next connection with no tables.
        conn = st.connect(self.db)
        for table in ("events", "proposals", "runs"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.close()
        self.run, self.pid = seed(self.db)
        self.wait_for_server()
        self.page = self.browser.new_page()
        self.posted = []
        self.complaints = []
        self.page.on("request", lambda r: self.posted.append(r.post_data)
                     if r.method == "POST" else None)
        self.page.on("console", lambda m: self.complaints.append(m.text)
                     if m.type == "error" else None)
        self.page.on("pageerror", lambda exc: self.complaints.append(str(exc)))
        self.page.goto(self.base + "/")

    def tearDown(self):
        self.page.close()

    def wait_for_server(self, seconds=20):
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                urllib.request.urlopen(self.base + "/healthz", timeout=1)
                return
            except urllib.error.HTTPError:
                return
            except Exception:
                time.sleep(0.2)
        self.fail("the server did not come up")

    def status(self):
        conn = st.connect(self.db)
        try:
            return conn.execute("SELECT * FROM proposals WHERE id = ?",
                                (self.pid,)).fetchone()
        finally:
            conn.close()

    def test_approve_reaches_the_server_with_its_action(self):
        self.page.click("button.approve")
        self.page.wait_for_load_state("load")
        self.assertIn("action=approve", self.posted[0])
        self.assertEqual(self.status()["status"], st.APPROVED)

    def test_approving_says_so_on_the_card(self):
        self.page.click("button.approve")
        self.page.wait_for_load_state("load")
        self.assertEqual(self.page.locator("p.state.approved").count(), 1)
        self.assertIn("Approved at 4",
                      self.page.inner_text("p.state.approved"))
        self.assertEqual(self.page.locator("button.approve").count(), 0)

    def test_the_bid_you_type_is_the_bid_that_is_approved(self):
        self.page.fill("input[name=bid]", "17")
        self.assertIn("Approve at 17", self.page.inner_text("button.approve"))
        self.page.click("button.approve")
        self.page.wait_for_load_state("load")
        self.assertEqual(self.status()["bid"], 17)

    def test_declining_reaches_the_server_too(self):
        self.page.click("button.decline")
        self.page.wait_for_load_state("load")
        self.assertEqual(self.status()["status"], st.DECLINED)

    def test_the_budget_line_follows_the_box(self):
        self.page.fill("input[name=bid]", "30")
        self.assertEqual(self.page.inner_text("[data-cost]"), "30")
        self.assertEqual(self.page.inner_text("[data-after]"), "70")

    def test_choosing_another_drop_restates_the_card(self):
        self.page.click(".picker > summary")
        self.page.check("input[value=d2]")
        self.assertIn("MarShawn Lloyd", self.page.inner_text("[data-dropname]"))
        self.assertIn("MarShawn Lloyd", self.page.inner_text("[data-dropwho]"))
        self.assertIn("2nd weakest", self.page.inner_text("[data-dropwhy]"))

    def test_nothing_on_the_page_throws(self):
        # Every handler on this page is delegated from the document, so one
        # exception in one of them silently stops the rest working.
        self.page.fill("input[name=bid]", "12")
        self.page.click(".picker > summary")
        self.page.check("input[value=d2]")
        self.assertEqual(self.complaints, [])

    def test_every_tab_loads(self):
        for path in ("/lineup", "/trades", "/scores", "/"):
            self.page.goto(self.base + path)
            self.assertEqual(self.page.locator("nav").count(), 1, path)
        self.assertEqual([c for c in self.complaints
                          if "favicon" not in c.lower()], [])

    def test_the_chosen_drop_is_the_one_recorded(self):
        self.page.click(".picker > summary")
        self.page.check("input[value=d2]")
        self.page.click("button.approve")
        self.page.wait_for_load_state("load")
        self.assertEqual(self.status()["drop_player_name"],
                         "MarShawn Lloyd (GB RB)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
