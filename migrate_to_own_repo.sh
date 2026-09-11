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
  check_sources.py claim_safety.py expert_extract.py expert_waivers.py
  install_schedule.py run_weekly.py sleeper_client.py source_discovery.py
  store.py submitter.py waiver_analyzer.py webapp.py yahoo_auth_check.py
  selectors.json sources.json .env.example README.md
)

echo "==> creating $DEST"
mkdir -p "$DEST"
for f in "${FILES[@]}"; do
  [ -f "$SRC/$f" ] && cp "$SRC/$f" "$DEST/" && echo "    $f"
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
if [ ! -d .git ]; then
  git init -q -b main
  git add .
  git commit -q -m "Fantasy football waiver agent

Reads analyst waiver articles, cross-references them against live league
rosters and roster needs, files proposals for approval on a phone-friendly
page, and places the approved claims in Sleeper.

Moved out of the fire-calc repository, where it was developed."
  echo "==> committed $(git rev-list --count HEAD) commit"
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
