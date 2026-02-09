#!/bin/bash
# Deploy new portfolio to GitHub
# Run this from the directory containing the extracted repo files.
#
# Prerequisites:
#   - git installed and authenticated (SSH key or token)
#   - GitHub CLI (gh) optional but recommended
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh

set -e

REPO_URL="git@github.com:Chrislundh98/DevOps-Media-Stack.git"
BRANCH="main"
TEMP_DIR=$(mktemp -d)

echo "=== Step 1: Clone existing repo ==="
git clone "$REPO_URL" "$TEMP_DIR/repo"
cd "$TEMP_DIR/repo"

echo "=== Step 2: Remove all existing content ==="
git rm -rf . 2>/dev/null || true
git clean -fd

echo "=== Step 3: Copy new content ==="
# Copy everything from the script's original directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp -r "$SCRIPT_DIR"/* "$TEMP_DIR/repo/" 2>/dev/null || true
cp "$SCRIPT_DIR"/.gitignore "$TEMP_DIR/repo/" 2>/dev/null || true
cp "$SCRIPT_DIR"/.env.template "$TEMP_DIR/repo/" 2>/dev/null || true

# Remove the deploy script itself from the repo
rm -f "$TEMP_DIR/repo/deploy.sh"

echo "=== Step 4: Stage all changes ==="
git add -A

echo "=== Step 5: Commit ==="
git commit -m "Complete portfolio restructure

- Reorganized into project-based structure
- Added tracker automation system (5,400+ lines)
- Added clan war Discord bot (3,400+ lines)
- Added PhD position monitor
- Added media processing tools
- Added infrastructure scripts
- Cleaned all sensitive data
- Professional documentation"

echo "=== Step 6: Force push ==="
echo ""
echo "Ready to push. This will REPLACE all content on $BRANCH."
read -p "Continue? (y/N) " confirm
if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
    git push origin "$BRANCH" --force
    echo "=== Done! ==="
    echo "Visit: https://github.com/Chrislundh98/DevOps-Media-Stack"
else
    echo "Aborted. Your staged commit is in: $TEMP_DIR/repo"
    echo "You can push manually with: cd $TEMP_DIR/repo && git push origin $BRANCH --force"
fi
