# fantasy-agent

Reads the waiver-wire articles you already read, works out which of their
recommendations are actually available in your leagues, proposes specific
add/drop/bid moves, and — once you approve them — places the claims.

Tuesday morning a scheduled job files proposals. You open a page on your
phone and approve, decline or re-bid each one. The submitter places what you
approved and reads the league back to prove it landed. Waivers process
Wednesday overnight, so there is a full day to audit or cancel.

## Setup

Python 3.8+, standard library only — except the submitter, which needs
Playwright to drive a browser.

```bash
python3 sleeper_client.py YOUR_SLEEPER_USERNAME     # confirm reads work
python3 install_schedule.py --user YOUR_USERNAME --host 0.0.0.0
```

That installs two background services: the weekly job (Tuesday 08:30) and the
approval page, which stays up and restarts itself. `--status` reports both,
and prints the URLs the page is reachable at, phone included.

For submitting claims:

```bash
pip3 install playwright && python3 -m playwright install chromium
python3 submitter.py YOUR_USERNAME --login    # starts Chrome; you log in
python3 submitter.py YOUR_USERNAME            # dry run
python3 submitter.py YOUR_USERNAME --submit   # place approved claims
python3 submitter.py YOUR_USERNAME --audit    # what is really queued
```

## How it fits together

| file | role |
|---|---|
| `sleeper_client.py` | Sleeper reads: leagues, rosters, matchups, players |
| `source_discovery.py` | finds this week's article from a stable hub or feed |
| `expert_extract.py` | pulls players and FAAB figures out of article prose |
| `waiver_analyzer.py` | ranks free agents; also scores drop candidates |
| `expert_waivers.py` | the analyst-consensus report (CLI) |
| `run_weekly.py` | the scheduled job: discover, analyse, file proposals |
| `store.py` | SQLite: proposals, decisions, audit events |
| `webapp.py` | the approval page |
| `claim_safety.py` | pre-flight checks and API read-back |
| `submitter.py` | drives Sleeper's UI to place approved claims |
| `check_sources.py` | is a candidate site usable as a source? |
| `selectors.json` | Sleeper's DOM selectors, kept out of the code |

## Safety model

Nothing reaches a league without a recorded approval.

- Proposals start `pending`. The submitter's only query is
  approved-and-unsubmitted, so pending and declined rows are invisible to it.
- Each proposal carries an idempotency key, so re-running the weekly job
  cannot duplicate something already acted on.
- Before each claim, the proposal is re-checked against the live league:
  a player claimed by someone else, a drop that left the roster or became a
  starter, or a bid that no longer fits the budget stops that claim.
- After each claim, the league is re-read through the public API. A claim is
  recorded as submitted only if Sleeper confirms it exists. The browser's own
  report of success is never taken as proof.
- A failure before the submit button leaves the claim approved and
  retryable. Only a pressed claim is ever retired, and `--retry` cannot
  resurrect one, since that would place it twice.

## Things that cost time to learn

**Yahoo's Fantasy API is gated and read-only.** The permission no longer
appears on the app-creation form; access needs a reviewed application at
`sports.yahoo.com/developer/access`, and Yahoo states write access is
unavailable. Lineup and waiver submission therefore cannot go through their
API by anyone. `yahoo_auth_check.py` re-tests this if it ever changes.

**Sleeper has no write API, and its UI uses almost no semantic HTML.** No
`<select>`; controls are clickable `div`s; the only `<button>` on the page is
the final `MAKE WAIVER BID`. Names are abbreviated in the players table
(`T. Ferguson`) but not in the claim dialog (`Woody Marks`). The drop is an
`<a class="team-roster-item">` — clicking the player's name inside it does
nothing. Selection is confirmed by the row changing, not by the "select a
player to drop" warning, which stays on screen regardless. Run
`submitter.py USER --probe LEAGUE_ID --player 'Full Name'` after any
redesign; it reports the page's real controls.

**Sleeper indexes transactions by the week they process in**, so a claim
placed today is filed under next week.

**Percentages in articles are mostly not bids.** "Rostered in 73% of
leagues" and "85% route share" are far more common than a FAAB figure, and
reading them as bids produces confident, badly wrong advice. Extraction
requires a bid cue and rejects stat cues.

## Known limits

- Yahoo leagues are unsupported: no reads, no writes.
- No lineup/start-sit recommendations yet.
- Extraction can attribute a recommendation to a player merely discussed in
  someone else's write-up. Each proposal carries the source sentence — read
  it before approving.
- Video and podcast pages are unusable as sources; `check_sources.py`
  identifies them.

## Running the page in the cloud

The page and database can run on a small always-on host while the submitter
stays on your Mac, where the logged-in browser session lives. That is what
lets you approve from anywhere, and it decouples the two halves: approving is
time-sensitive and happens wherever you are, submitting can happen any time
before waivers process.

The Mac is never reached from outside. The submitter polls the host over
HTTPS, so it works behind any router with no VPN, no open ports and no fixed
address.

```bash
brew install flyctl && fly auth login

fly launch --no-deploy          # uses the included fly.toml and Dockerfile
fly volumes create fantasy_data --size 1 --region iad

fly secrets set \
  FANTASY_PASSWORD='pick something long' \
  FANTASY_SECRET="$(python3 -c 'import secrets;print(secrets.token_hex(32))')" \
  FANTASY_API_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  FANTASY_USER=YOUR_SLEEPER_USERNAME \
  FANTASY_SCHEDULE='tue 08:30'

fly deploy
fly open                        # your URL, reachable from anywhere
```

`FANTASY_USER` tells the host whose leagues to read. The job itself is
triggered from outside, by the GitHub Action in `.github/workflows/`, which
POSTs to `/cron/weekly` on Tuesday morning. That request is also what wakes
the machine, so it can sleep the rest of the week instead of idling at full
price to serve a few minutes of work. Set `FANTASY_API_URL` and
`FANTASY_API_TOKEN` as repository secrets for the Action to use.

The endpoint refuses to run twice in the same week, because a second run
would re-propose everything: the idempotency key includes the run, so a
fresh run looks entirely new.

`FANTASY_SCHEDULE` is the alternative - an in-process timer, for a host that
is always on anyway. Setting it means the machine must not be allowed to
sleep, which is what makes it the expensive option.

Read the token back for the local half with `fly secrets list` (it shows
digests only, so keep the value when you generate it).

Then point the local half at it, using the same token:

```bash
export FANTASY_API_URL=https://your-app.fly.dev
export FANTASY_API_TOKEN=...        # the value set above
python3 submitter.py YOUR_USERNAME --submit
```

`run_weekly.py` also needs to write to the host rather than a local file. Run
it on the host itself (a scheduled machine), or keep it on the Mac and point
`--db` at a copy you sync — the first is simpler.

Two credentials, deliberately separate: `FANTASY_PASSWORD` is what you type
in a browser, `FANTASY_API_TOKEN` is what the submitter sends. Either can be
rotated without disturbing the other. Sessions are signed rather than stored,
so a redeploy does not log you out.
