# Undoing Mistakes in Git

The "oh shit" guide. When things go wrong, don't panic — git keeps everything.

---

## Undo Uncommitted Changes

**Discard changes to a file (go back to last commit):**
```bash
git restore file.py              # Discard changes in working directory
git restore .                    # Discard ALL uncommitted changes
```

**Unstage a file (undo git add, keep changes):**
```bash
git restore --staged file.py
git restore --staged .           # Unstage everything
```

---

## Undo the Last Commit

**Keep changes, just undo the commit:**
```bash
git reset --soft HEAD~1          # Changes stay staged
git reset HEAD~1                 # Changes become unstaged (default: --mixed)
```

**Nuke the commit and all changes:**
```bash
git reset --hard HEAD~1          # ⚠️ Destroys changes! Use with caution.
```

**Undo last 3 commits (keep changes):**
```bash
git reset --soft HEAD~3
```

---

## Fix the Last Commit

**Change the commit message:**
```bash
git commit --amend -m "Better commit message"
```

**Add a forgotten file to the last commit:**
```bash
git add forgotten_file.py
git commit --amend --no-edit     # Adds to last commit without changing message
```

> **Note:** Only amend commits you haven't pushed yet. If already pushed, use `git revert` instead.

---

## Revert a Pushed Commit

When a commit is already on GitHub, create a new commit that undoes it:

```bash
git revert abc1234               # Creates a new "undo" commit
git revert HEAD                  # Revert the most recent commit
git push
```

This is safe because it doesn't rewrite history.

---

## Recover Deleted Stuff

**Recover a deleted file (if committed before):**
```bash
git checkout HEAD -- deleted_file.py
```

**Recover a deleted branch:**
```bash
git reflog                       # Find the commit hash
git checkout -b recovered-branch abc1234
```

---

## The Reflog — Your Safety Net

git reflog records EVERYTHING you've done, even after resets and deletes.

```bash
git reflog                       # Show all recent actions
git reflog --oneline             # Compact view
```

Example output:
```
abc1234 HEAD@{0}: reset: moving to HEAD~1
def5678 HEAD@{1}: commit: Add: new feature
ghi9012 HEAD@{2}: commit: Fix: bug
```

**Recover from a bad reset:**
```bash
git reflog                       # Find the hash before the mistake
git reset --hard def5678         # Go back to that point
```

---

## Nuclear Options (Use Carefully)

**Throw away ALL local changes and match remote:**
```bash
git fetch origin
git reset --hard origin/main
```

**Remove untracked files:**
```bash
git clean -n                     # Dry run — show what would be deleted
git clean -f                     # Delete untracked files
git clean -fd                    # Delete untracked files AND directories
```

**Remove a file from git history** (e.g., accidentally committed .env):
```bash
# Install git-filter-repo first (better than filter-branch)
pip install git-filter-repo

# Remove a file from all history
git filter-repo --path .env --invert-paths

# Force push (rewrites remote history)
git push --force
```

> **Warning:** `git filter-repo` rewrites history. All collaborators need to re-clone. For a solo portfolio repo this is fine.

---

## Quick Decision Guide

| Situation | Command |
|---|---|
| Changed a file, want to undo | `git restore file.py` |
| Staged a file, want to unstage | `git restore --staged file.py` |
| Committed but not pushed, undo | `git reset --soft HEAD~1` |
| Committed AND pushed, undo safely | `git revert HEAD` |
| Wrong commit message | `git commit --amend -m "new msg"` |
| Forgot to add a file to commit | `git add file && git commit --amend --no-edit` |
| Everything is broken | `git reflog` → find good state → `git reset --hard <hash>` |
| Match remote exactly | `git fetch origin && git reset --hard origin/main` |
| Accidentally committed secrets | `git filter-repo --path .env --invert-paths` |
