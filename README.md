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
| `lineup.py` | start/sit recommendations for the coming week |
| `check_sources.py` | is a candidate site usable as a source? |
| `selectors.json` | Sleeper's DOM selectors, kept out of the code |

## Start/sit

```bash
python3 lineup.py YOUR_SLEEPER_USERNAME
```

Compares your starters against your bench using projections scored by each
league's own rules, which matters when leagues differ: 4 versus 6 points for
a passing touchdown makes the same quarterback worth visibly different
amounts, so one ranking cannot serve both.

An unavailable starter is always replaced; a projected gain is only worth
acting on above a small threshold, below which the projection is noise. A
player who is Out is never suggested as a replacement however well he
projects.

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
- Extraction can attribute a recommendation to a player merely discussed in
  someone else's write-up. Each proposal carries the source sentence — read
  it before approving.
- Video and podcast pages are unusable as sources; `check_sources.py`
  identifies them.

## Running the page in the cloud

The page and its database can be hosted while the submitter stays on your
Mac, where the logged-in browser session lives. That is what lets you approve
from anywhere, and it decouples the halves: approving is time-sensitive and
happens wherever you are, submitting can happen any time before waivers
process. The Mac is never reached from outside - it polls the host over
HTTPS - so it works behind any router with no VPN or open ports.

Three parts, because a serverless host cannot do all of it:

| where | does what | why there |
|---|---|---|
| Vercel | serves the page, holds the database | always on, public URL |
| GitHub Actions | runs the weekly job, posts the proposals | the job fetches megabytes and needs minutes, far longer than a function may live |
| your Mac | places approved claims in a browser | the logged-in session is here |

### Deploying

Create a Postgres database - Supabase, Neon and Vercel Postgres all have
free tiers and all work unchanged, since this only needs plain Postgres.

On Supabase, take the **Transaction pooler** connection string (port 6543),
not the direct one. A serverless function opens a connection per request and
throws it away; direct connections are limited and would be exhausted, while
the pooler is built for exactly this. TLS is required and is added
automatically if the string does not specify it.

Then:

```bash
npm i -g vercel && vercel login
vercel link
vercel env add DATABASE_URL          # the Postgres connection string
vercel env add FANTASY_PASSWORD      # what you type in the browser
vercel env add FANTASY_SECRET        # python3 -c 'import secrets;print(secrets.token_hex(32))'
vercel env add FANTASY_API_TOKEN     # python3 -c 'import secrets;print(secrets.token_urlsafe(32))'
vercel --prod
```

Then set three repository secrets in GitHub (Settings, Secrets and
variables, Actions) so the weekly job can reach it: `FANTASY_API_URL` (your
Vercel URL), `FANTASY_API_TOKEN` (the same token), and `FANTASY_USER` (your
Sleeper username). The Action runs Tuesday morning and can also be run by
hand from the Actions tab.

Finally, point the local half at the host, in `~/.zshrc`:

```bash
export FANTASY_API_URL=https://your-app.vercel.app
export FANTASY_API_TOKEN=...        # the same token again
```

`submitter.py` then reads approved claims from the host instead of a local
file, and reports back what happened.

### Storage

`DATABASE_URL` decides the backend: set, it is Postgres; unset, a SQLite file
next to the code. The same schema and the same code run on both, so local
runs need nothing installed and the hosted one needs no special casing.

Two credentials, deliberately separate: `FANTASY_PASSWORD` is typed in a
browser and establishes a signed session; `FANTASY_API_TOKEN` is what the
submitter and the weekly job send. Either can be rotated without disturbing
the other, and sessions are signed rather than stored, so a redeploy does not
log you out.
