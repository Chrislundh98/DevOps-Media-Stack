# Bash Essential Commands

Quick reference for everyday Linux/NAS terminal work.

---

## Navigation & File Operations

```bash
pwd                          # Print current directory
ls                           # List files
ls -la                       # List ALL files (hidden too) with details
ls -lah                      # Same but human-readable sizes (KB, MB, GB)
cd /volume1/automation       # Go to directory
cd ..                        # Go up one level
cd ~                         # Go to home directory
cd -                         # Go back to previous directory
```

**Creating and removing:**
```bash
mkdir my_folder              # Create folder
mkdir -p path/to/deep/folder # Create nested folders (all at once)
touch file.txt               # Create empty file
cp file.txt backup.txt       # Copy file
cp -r folder/ backup/        # Copy folder recursively
mv old.txt new.txt           # Rename or move file
rm file.txt                  # Delete file (no recycle bin!)
rm -rf folder/               # Delete folder and everything inside (CAREFUL)
```

**Wildcards:**
```bash
ls *.py                      # All Python files
ls *.log                     # All log files
rm -f *.pyc                  # Delete all compiled Python files
cp *.json /backup/           # Copy all JSON files
```

---

## Viewing & Reading Files

```bash
cat file.txt                 # Print entire file
head -20 file.txt            # First 20 lines
tail -20 file.txt            # Last 20 lines
tail -f /app/logs/app.log    # Follow log in real-time (Ctrl+C to stop)
less file.txt                # Scrollable viewer (q to quit, / to search)
wc -l file.txt               # Count lines in file
```

---

## Finding Things

**Find files by name:**
```bash
find . -name "*.py"                     # All .py files from current dir
find /volume1 -name "docker-compose*"   # Find compose files
find . -name "*.log" -delete            # Find AND delete all logs
find . -type f -size +100M              # Files over 100MB
find . -type f -mtime -1                # Files modified in last 24h
find . -type d -name "logs"             # Find directories named "logs"
```

**Search inside files with grep:**
```bash
grep "error" app.log                    # Lines containing "error"
grep -i "error" app.log                 # Case-insensitive
grep -r "webhook" .                     # Recursive search in all files
grep -rn "TODO" --include="*.py" .      # Search .py files, show line numbers
grep -c "error" app.log                 # Count matching lines
grep -v "DEBUG" app.log                 # Lines NOT containing "DEBUG"
grep -rniE "token|key|secret|password" .  # Security scan (regex, case-insensitive)
```

---

## Permissions & Ownership

```bash
chmod +x script.sh           # Make file executable
chmod 755 script.sh          # rwx for owner, rx for group & others
chmod 644 file.txt           # rw for owner, r for group & others
chown user:group file.txt    # Change owner
chown -R 1000:1000 /path/    # Recursive ownership change (common Docker fix)
```

**Quick permission reference:**
| Number | Permission |
|--------|-----------|
| 7 | rwx (read + write + execute) |
| 6 | rw- (read + write) |
| 5 | r-x (read + execute) |
| 4 | r-- (read only) |
| 0 | --- (no access) |

---

## Disk Usage

```bash
df -h                        # Disk space on all drives
df -h /volume1               # Disk space on specific volume
du -sh *                     # Size of each item in current dir
du -sh * | sort -hr          # Same, sorted largest first
du -sh /volume1/media        # Total size of a directory
du -sh --max-depth=1 /volume1  # Size breakdown one level deep
ncdu /volume1                # Interactive disk usage (if installed)
```

---

## Process Management

```bash
ps aux                       # All running processes
ps aux | grep python         # Find python processes
top                          # Live process monitor (q to quit)
htop                         # Better process monitor (if installed)
kill PID                     # Gracefully stop a process
kill -9 PID                  # Force kill a process
pkill -f "bot.py"            # Kill process by name match
nohup python bot.py &        # Run in background, survives logout
jobs                         # List background jobs
fg                           # Bring background job to foreground
```

---

## Environment Variables

```bash
echo $HOME                   # Print a variable
export MY_VAR="hello"        # Set variable for current session
env                          # Show all environment variables
env | grep DISCORD           # Filter for specific vars
source .env                  # Load variables from file (simple key=value files)
printenv MY_VAR              # Print specific variable
```

---

## Archives & Compression

```bash
# Create tar.gz
tar -czf backup.tar.gz folder/

# Extract tar.gz
tar -xzf backup.tar.gz

# Extract to specific directory
tar -xzf backup.tar.gz -C /target/dir/

# Create zip
zip -r archive.zip folder/

# Extract zip
unzip archive.zip
unzip archive.zip -d /target/dir/

# List contents without extracting
tar -tzf backup.tar.gz
unzip -l archive.zip
```

---

## Piping & Redirection

```bash
# Pipe: send output of one command to another
ls -la | grep ".py"                  # List only python files
cat log.txt | sort | uniq            # Sort and remove duplicates
ps aux | grep python | wc -l         # Count python processes

# Redirect output to file
echo "hello" > file.txt              # Write (overwrites!)
echo "world" >> file.txt             # Append
command 2>&1                         # Redirect errors to stdout
command > output.txt 2>&1            # All output to file
command > /dev/null 2>&1             # Silence everything
```

---

## Useful Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+C` | Stop current command |
| `Ctrl+Z` | Suspend current command (resume with `fg`) |
| `Ctrl+D` | Exit shell / end input |
| `Ctrl+R` | Search command history |
| `Ctrl+L` | Clear screen |
| `Tab` | Auto-complete file/command names |
| `Tab Tab` | Show all completions |
| `!!` | Repeat last command |
| `!$` | Last argument of previous command |
| `Ctrl+A` | Jump to start of line |
| `Ctrl+E` | Jump to end of line |
| `Ctrl+W` | Delete word before cursor |

---

## Miscellaneous

```bash
history                      # Show command history
history | grep docker        # Search history
date                         # Current date/time
uptime                       # System uptime and load
whoami                       # Current user
which python3                # Path to a command
alias ll='ls -la'            # Create alias (add to .bashrc to persist)
watch -n 5 'df -h'           # Run command every 5 seconds
xargs                        # Build commands from stdin
echo "hello" | xargs echo    # Example: pipe as arguments
```
