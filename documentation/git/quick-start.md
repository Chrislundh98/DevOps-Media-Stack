# Git Quick-Start

Everything you need for day-to-day git usage. No fluff.

---

## First Time Setup

```bash
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
git config --global init.defaultBranch main

# Verify
git config --list
```

---

## Starting a Repo

**Option A: New repo from scratch:**
```bash
mkdir my-project && cd my-project
git init
git remote add origin https://github.com/username/repo.git
```

**Option B: Clone existing repo:**
```bash
git clone https://github.com/username/repo.git
cd repo
```

**Option C: Existing folder → GitHub:**
```bash
cd /volume1/portfolio
git init
git remote add origin https://github.com/username/portfolio.git
git add .
git commit -m "Initial commit"
git push -u origin main
```

---

## The Daily Workflow

This is what you'll do 95% of the time:

```bash
# 1. Check what's changed
git status

# 2. Stage changes
git add filename.py              # Stage specific file
git add .                        # Stage everything

# 3. Commit
git commit -m "Fix: torrent matching edge case"

# 4. Push to GitHub
git push
```

That's it. `status → add → commit → push`.

---

## Checking Things

```bash
git status                       # What's changed? What's staged?
git diff                         # See exact changes (unstaged)
git diff --staged                # See exact changes (staged, about to commit)
git log                          # Commit history
git log --oneline                # Compact history
git log --oneline -10            # Last 10 commits
git log --graph --oneline        # Visual branch graph
```

---

## Staging (git add)

```bash
git add file.py                  # Stage one file
git add folder/                  # Stage entire folder
git add .                        # Stage everything in current directory
git add -A                       # Stage everything (including deletions)
git add *.py                     # Stage all Python files
git add -p                       # Interactive: choose which changes to stage
```

**Unstage (undo git add):**
```bash
git restore --staged file.py     # Unstage file (keep changes)
git restore --staged .           # Unstage everything
```

---

## Committing

```bash
git commit -m "Short description"

# Multi-line commit message
git commit -m "Title line" -m "More details about the change"

# Stage + commit in one step (only tracked files)
git commit -am "Fix: typo in config"
```

**Good commit messages:**
```
Add: Discord notification for new torrents
Fix: matching algorithm false positives on anime
Update: bandwidth manager threshold from 50 to 80 Mbps
Remove: unused chrome profile cleanup
Refactor: split torrentleech.py into modules
```

---

## Pushing & Pulling

```bash
git push                         # Push to remote (after initial setup)
git push -u origin main          # First push (sets upstream)
git pull                         # Fetch + merge remote changes
git fetch                        # Fetch without merging (just check)
```

---

## Remote Repos

```bash
git remote -v                    # Show remote URLs
git remote add origin URL        # Add remote
git remote set-url origin URL    # Change remote URL
git remote remove origin         # Remove remote
```

**Switch from HTTPS to SSH:**
```bash
git remote set-url origin git@github.com:username/repo.git
```

---

## Ignoring Files

Create a `.gitignore` file:
```bash
echo ".env" >> .gitignore
echo "*.log" >> .gitignore
echo "__pycache__/" >> .gitignore
```

See the full [.gitignore guide](gitignore.md).

---

## Quick Reference Card

| What you want | Command |
|---|---|
| What changed? | `git status` |
| See exact changes | `git diff` |
| Stage file | `git add file` |
| Stage everything | `git add .` |
| Commit | `git commit -m "message"` |
| Push | `git push` |
| Pull | `git pull` |
| History | `git log --oneline` |
| Undo staged file | `git restore --staged file` |
| Discard local changes | `git restore file` |
| Create branch | `git checkout -b name` |
| Switch branch | `git checkout name` |
| Merge branch | `git merge name` |
