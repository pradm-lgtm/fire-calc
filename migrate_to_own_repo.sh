#!/usr/bin/env bash
# Move the fantasy agent out of the fire-calc repo into its own.
#
#   1. At https://github.com/new create a repo named waiver-agent.
#      Leave "Add a README file", "Add .gitignore" and "Choose a license"
#      UNCHECKED - an initialised repo already has a commit, which this
#      push would then collide with.
#      (pradm-lgtm/fantasy-agent holds an older version of this project and
#       is being kept as an archive, hence the new name.)
#   2. bash migrate_to_own_repo.sh
#
# Copies only the agent's files - the calculator's package.json, src/ and so
# on stay behind - and carries over your .env and database if they exist, so
# nothing has to be set up again.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-$HOME/waiver-agent}"
REMOTE="${2:-https://github.com/pradm-lgtm/waiver-agent.git}"

FILES=(
  check_sources.py claim_safety.py cloud_auth.py cloud_client.py
  expert_extract.py expert_waivers.py install_schedule.py run_weekly.py
  sleeper_client.py source_discovery.py store.py submitter.py
  waiver_analyzer.py webapp.py yahoo_auth_check.py
  db.py lineup.py localenv.py rankings.py render.py
  test_rankings.py test_lineup.py test_localenv.py
  test_webapp.py
  never_drops.json selectors.json sources.json ranking_sources.json
  vercel.json requirements.txt
  Dockerfile fly.toml .env.example README.md
)

# Copied with their directory structure rather than flattened into the root.
TREES=(api .github)

# Every module the agent imports, checked after copying. A hand-kept file
# list silently drops whatever was added since it was written, and the first
# symptom is the page failing to start.
ENTRYPOINTS=(webapp submitter run_weekly lineup expert_waivers check_sources
             source_discovery install_schedule)

echo "==> creating $DEST"
mkdir -p "$DEST"
for f in "${FILES[@]}"; do
  [ -f "$SRC/$f" ] && cp "$SRC/$f" "$DEST/" && echo "    $f"
done
for t in "${TREES[@]}"; do
  if [ -d "$SRC/$t" ]; then
    cp -R "$SRC/$t" "$DEST/" && echo "    $t/"
  fi
done
cp "$SRC/gitignore.fantasy" "$DEST/.gitignore" 2>/dev/null \
  || cp "$SRC/.gitignore.fantasy" "$DEST/.gitignore"

# Carry over live state so nothing needs redoing. Neither is committed.
for state in .env fantasy.db; do
  if [ -e "$SRC/$state" ]; then
    cp -R "$SRC/$state" "$DEST/" && echo "    $state (kept local, not committed)"
  fi
done

cd "$DEST"

echo "==> checking every entrypoint imports"
missing=0
for mod in "${ENTRYPOINTS[@]}"; do
  if ! python3 -c "import $mod" 2>/dev/null; then
    echo "    BROKEN: $mod - something it imports was not copied"
    python3 -c "import $mod" 2>&1 | tail -1 | sed 's/^/      /'
    missing=1
  fi
done
if [ "$missing" = "1" ]; then
  echo
  echo "Refusing to commit an installation that cannot start."
  echo "Add the missing file(s) to FILES in $0 and re-run."
  exit 1
fi
echo "    all good"

echo "==> running tests"
for t in test_*.py; do
  [ -f "$t" ] || continue
  if ! python3 "$t" 2>&1 | tail -3 | sed "s/^/    $t /"; then
    echo "Refusing to commit with failing tests."
    exit 1
  fi
done

if [ ! -d .git ]; then
  git init -q -b main
  git add .
  git commit -q -m "Fantasy football waiver agent

Reads analyst waiver articles, cross-references them against live league
rosters and roster needs, files proposals for approval on a phone-friendly
page, and places the approved claims in Sleeper.

Moved out of the fire-calc repository, where it was developed."
  echo "==> committed $(git rev-list --count HEAD) commit"
else
  # Re-running this is how changes made in fire-calc reach the live repo, so
  # an update has to commit too; the first run is the only one that does not.
  git add -A
  if git diff --cached --quiet; then
    echo "==> no changes to commit"
  else
    git commit -q -m "Sync agent files from the development copy"
    echo "==> committed the updated files"
  fi
fi

git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE"
echo
echo "==> ready. push with:"
echo "      cd $DEST && git push -u origin main"
echo
echo "Then re-point the background services at the new location:"
echo "      cd $SRC && python3 install_schedule.py --uninstall"
echo "      cd $DEST && python3 install_schedule.py --user pradm7 --host 0.0.0.0"
