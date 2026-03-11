#!/bin/sh
# Push all changes to GitHub (run from repo root)
MSG="${1:-Update}"
cd "$(dirname "$0")/.."
git add -A
git status --short
git commit -m "$MSG" && git push origin main
