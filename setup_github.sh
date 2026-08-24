#!/usr/bin/env bash
# One-shot GitHub setup for the ONS balances dashboard.
#
#   ./setup_github.sh <repo-name> [--private]
#
# Creates the repo, pushes this directory, turns on Pages with GitHub Actions
# as the build source, kicks off the first build, and prints the URL.
#
# Needs the GitHub CLI, authenticated:
#     https://cli.github.com   then:  gh auth login
#
# Re-running against an existing repo is safe: it skips creation and pushes.

set -euo pipefail

REPO="${1:-ons-balances}"
VIS="--public"
[[ "${2:-}" == "--private" ]] && VIS="--private"

need() { command -v "$1" >/dev/null || { echo "Missing: $1"; exit 1; }; }
need git
need gh

gh auth status >/dev/null 2>&1 || { echo "Run 'gh auth login' first."; exit 1; }
OWNER=$(gh api user --jq .login)
echo "==> Account: $OWNER"

if [[ "$VIS" == "--private" ]]; then
  cat <<'WARN'
NOTE: a private repo means GitHub Pages will NOT serve the site unless you are on
GitHub Enterprise Cloud. Everything else here still works; you just won't get a
public URL. See the "If you later want it private" section of the README.
WARN
fi

# ---------------------------------------------------------------- git history
if [[ ! -d .git ]]; then
  echo "==> Initialising repository"
  git init -q
  git branch -M main
fi
git add -A
git diff --staged --quiet || git commit -q -m "ONS balances dashboard: pipeline, dashboard, daily refresh workflow"
echo "==> Commit ready"

# ------------------------------------------------------------------- the repo
if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  echo "==> Repo $OWNER/$REPO already exists - reusing it"
  git remote get-url origin >/dev/null 2>&1 \
    || git remote add origin "https://github.com/$OWNER/$REPO.git"
else
  echo "==> Creating $OWNER/$REPO"
  gh repo create "$REPO" $VIS \
    --source=. --remote=origin \
    --description="Brazilian grid balances from ONS open data, refreshed daily"
fi

echo "==> Pushing"
git push -u origin main

# ------------------------------------------------------------------ Pages on
echo "==> Enabling Pages (source: GitHub Actions)"
if gh api "repos/$OWNER/$REPO/pages" >/dev/null 2>&1; then
  gh api -X PUT "repos/$OWNER/$REPO/pages" -f build_type=workflow >/dev/null \
    && echo "    updated existing Pages config"
else
  gh api -X POST "repos/$OWNER/$REPO/pages" -f build_type=workflow >/dev/null \
    && echo "    Pages enabled" \
    || echo "    could not enable automatically - do it by hand:
    Settings -> Pages -> Build and deployment -> Source: GitHub Actions"
fi

# --------------------------------------------------------------- first build
echo "==> Starting the first build (this one takes 10-20 minutes)"
sleep 3
gh workflow run refresh.yml --repo "$OWNER/$REPO" 2>/dev/null \
  && echo "    started" \
  || echo "    start it by hand: Actions tab -> Refresh ONS dashboard -> Run workflow"

cat <<EOF

Done.

  Repo      https://github.com/$OWNER/$REPO
  Actions   https://github.com/$OWNER/$REPO/actions
  Site      https://$OWNER.github.io/$REPO/     (live once the first run finishes)

Watch the first run with:   gh run watch --repo $OWNER/$REPO
EOF
