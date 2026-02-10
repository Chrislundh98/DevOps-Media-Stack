# Nano Cheatsheet

The simple terminal text editor — perfect for quick edits on the NAS over SSH.

---

## Opening Files

```bash
nano file.txt                    # Open or create file
nano +15 file.txt                # Open at line 15
sudo nano /etc/config            # Edit system files
```

---

## Essential Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+O` | **Save** (Write Out) — press Enter to confirm |
| `Ctrl+X` | **Exit** (prompts to save if modified) |
| `Ctrl+K` | **Cut** entire line |
| `Ctrl+U` | **Paste** cut line |
| `Ctrl+W` | **Search** — type query, Enter to find |
| `Ctrl+\` | **Search & Replace** |
| `Ctrl+G` | **Help** — show all shortcuts |

---

## Navigation

| Shortcut | Action |
|----------|--------|
| `Ctrl+A` | Go to beginning of line |
| `Ctrl+E` | Go to end of line |
| `Ctrl+Y` | Page up |
| `Ctrl+V` | Page down |
| `Ctrl+_` | Go to line number |
| `Ctrl+C` | Show current line/column number |

---

## Editing

| Shortcut | Action |
|----------|--------|
| `Ctrl+K` | Cut line (or selection) |
| `Ctrl+U` | Paste |
| `Alt+6` | Copy line (without cutting) |
| `Ctrl+Shift+K` | Delete line |
| `Alt+U` | Undo |
| `Alt+E` | Redo |

---

## Selection

| Shortcut | Action |
|----------|--------|
| `Alt+A` | Start selecting (mark) |
| Move cursor | Extends selection |
| `Ctrl+K` | Cut selection |
| `Alt+6` | Copy selection |

---

## Search & Replace

**Search:** `Ctrl+W` → type term → Enter → `Alt+W` to find next

**Replace:** `Ctrl+\` → type search term → Enter → type replacement → Enter
- `Y` = replace this one
- `N` = skip this one
- `A` = replace ALL

---

## Quick Workflow

```bash
# Edit a file
nano /volume1/automation/trackers/core/torrentleech.py

# 1. Make changes
# 2. Ctrl+O → Enter (save)
# 3. Ctrl+X (exit)

# Or: Ctrl+X → Y → Enter (save and exit in one flow)
```
