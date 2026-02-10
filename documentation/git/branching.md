# Git Branching & Merging

Branches let you work on features/fixes without touching the main code.

---

## Branch Basics

```bash
git branch                       # List local branches (* = current)
git branch -a                    # List all branches (including remote)
git branch feature-name          # Create new branch
git checkout feature-name        # Switch to branch
git checkout -b feature-name     # Create AND switch in one step
git branch -d feature-name       # Delete branch (safe — won't delete unmerged)
git branch -D feature-name       # Force delete branch
```

---

## The Feature Branch Workflow

This is the standard way to work:

```bash
# 1. Start from main
git checkout main
git pull

# 2. Create feature branch
git checkout -b feature/discord-notifications

# 3. Work, commit, repeat
git add .
git commit -m "Add: Discord embed formatting"
git add .
git commit -m "Fix: webhook URL validation"

# 4. Switch back to main and merge
git checkout main
git pull                         # Get any changes others pushed
git merge feature/discord-notifications

# 5. Push and clean up
git push
git branch -d feature/discord-notifications
```

---

## Merging

```bash
# Merge a branch into current branch
git merge feature-name

# Merge with a commit message (no fast-forward)
git merge --no-ff feature-name

# Abort a merge if it goes wrong
git merge --abort
```

---

## Resolving Merge Conflicts

When git can't auto-merge, it marks the file with conflict markers:

```
<<<<<<< HEAD
your changes on main
=======
changes from the feature branch
>>>>>>> feature-name
```

**To fix:**
1. Open the file and edit it — keep what you want, delete the markers
2. Stage the resolved file: `git add file.py`
3. Commit: `git commit -m "Resolve merge conflict in file.py"`

**Tip:** Use `git diff` to see what conflicts remain before committing.

---

## Rebasing (Alternative to Merge)

Rebase replays your commits on top of another branch — makes cleaner history.

```bash
# Instead of merging main into your feature branch:
git checkout feature-name
git rebase main

# If conflicts, fix them then:
git add .
git rebase --continue

# Abort if things go wrong:
git rebase --abort
```

**Rule of thumb:** Use merge for shared branches, rebase for your own feature branches before merging.

---

## Remote Branches

```bash
# Push a new branch to GitHub
git push -u origin feature-name

# Track a remote branch
git checkout --track origin/feature-name

# Delete a remote branch
git push origin --delete feature-name

# See which remote branches exist
git branch -r

# Prune deleted remote branches locally
git fetch --prune
```

---

## Stashing (Temporary Save)

Save work-in-progress when you need to switch branches:

```bash
git stash                        # Stash current changes
git stash list                   # List all stashes
git stash pop                    # Restore latest stash (and remove it)
git stash apply                  # Restore latest stash (keep it in stash list)
git stash drop                   # Delete latest stash
git stash clear                  # Delete all stashes
git stash -m "WIP: half-done feature"  # Stash with description
```

**Common pattern:**
```bash
# Working on feature, need to switch to fix a bug:
git stash
git checkout main
# ... fix the bug, commit, push ...
git checkout feature-name
git stash pop                    # Resume where you left off
```
