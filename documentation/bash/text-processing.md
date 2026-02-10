# Text Processing — grep, sed, awk, sort, cut

Power tools for filtering, transforming, and analyzing text from the terminal.

---

## grep — Search & Filter

Already covered in [essential-commands.md](essential-commands.md), but here are the advanced patterns:

```bash
# Regex patterns
grep -E "error|warning|critical" app.log      # Match any of these words (extended regex)
grep -P "\d{1,3}\.\d{1,3}\.\d{1,3}" app.log  # Match IP-like patterns (Perl regex)

# Context around matches
grep -B 3 "error" app.log                     # 3 lines BEFORE each match
grep -A 3 "error" app.log                     # 3 lines AFTER each match
grep -C 3 "error" app.log                     # 3 lines before AND after

# Show only the match, not the whole line
grep -o "https://[^ ]*" file.txt              # Extract all URLs

# Invert (exclude lines)
grep -v "DEBUG" app.log                        # Everything except DEBUG lines
grep -v "^#" config.txt | grep -v "^$"         # Remove comments and blank lines
```

---

## sed — Find & Replace in Streams

sed edits text as it flows through — it doesn't open files in an editor.

```bash
# Basic find & replace (first match per line)
sed 's/old/new/' file.txt

# Replace ALL matches on each line
sed 's/old/new/g' file.txt

# Edit file in-place (changes the actual file)
sed -i 's/old/new/g' file.txt

# Delete lines matching a pattern
sed '/DEBUG/d' app.log                         # Remove all DEBUG lines
sed '/^$/d' file.txt                           # Remove blank lines

# Delete specific lines
sed '1d' file.txt                              # Delete first line
sed '1,5d' file.txt                            # Delete lines 1-5

# Insert text
sed '1i\# This is a header' file.txt           # Insert before line 1
sed '$a\# End of file' file.txt                # Append after last line

# Multiple operations
sed -e 's/foo/bar/g' -e 's/baz/qux/g' file.txt

# Print only matching lines (like grep)
sed -n '/error/p' app.log
```

**Common real-world uses:**
```bash
# Fix paths across files
sed -i 's|/volume1/old|/volume1/new|g' *.sh

# Strip trailing whitespace
sed -i 's/[[:space:]]*$//' file.txt

# Replace environment variable placeholder
sed -i "s|__WEBHOOK__|$DISCORD_WEBHOOK|g" config.template
```

---

## awk — Column-Based Processing

awk is amazing when your data has columns (space or tab separated).

```bash
# Print specific columns
awk '{print $1}' file.txt              # First column
awk '{print $1, $3}' file.txt          # First and third columns
awk '{print $NF}' file.txt             # Last column

# Custom delimiter
awk -F':' '{print $1}' /etc/passwd     # Split on colon
awk -F',' '{print $2}' data.csv        # Split on comma

# Filter rows
awk '$3 > 100' data.txt                # Rows where column 3 > 100
awk '/error/' app.log                  # Rows containing "error"
awk 'NR==5' file.txt                   # Only line 5
awk 'NR>=5 && NR<=10' file.txt         # Lines 5-10

# Math on columns
awk '{sum += $1} END {print sum}' nums.txt          # Sum of column 1
awk '{sum += $1} END {print sum/NR}' nums.txt       # Average

# Format output
awk '{printf "%-20s %s\n", $1, $2}' file.txt        # Aligned columns

# Count occurrences
awk '{count[$1]++} END {for (k in count) print k, count[k]}' file.txt
```

**Real-world examples:**
```bash
# Docker: list container names and status
docker ps --format "table {{.Names}}\t{{.Status}}" | awk 'NR>1 {print $1, $2}'

# Disk usage: show only directories over 1GB
du -sh * | awk '$1 ~ /G/ {print}'

# Extract IPs from a log
awk '{print $1}' access.log | sort | uniq -c | sort -rn | head -10
```

---

## sort — Ordering Lines

```bash
sort file.txt                          # Alphabetical sort
sort -r file.txt                       # Reverse sort
sort -n file.txt                       # Numeric sort
sort -hr file.txt                      # Human-readable numeric (1K, 2M, 3G)
sort -u file.txt                       # Sort and remove duplicates
sort -t',' -k2 data.csv               # Sort CSV by second column
sort -t',' -k2 -n data.csv            # Sort CSV by second column numerically
```

---

## cut — Extract Columns

```bash
cut -d':' -f1 /etc/passwd             # First field, colon-delimited
cut -d',' -f1,3 data.csv              # Fields 1 and 3 from CSV
cut -c1-10 file.txt                   # First 10 characters of each line
```

---

## uniq — Deduplicate (use after sort)

```bash
sort file.txt | uniq                   # Remove duplicates
sort file.txt | uniq -c                # Count occurrences
sort file.txt | uniq -d                # Show only duplicates
```

---

## Combining Everything — Pipeline Examples

```bash
# Top 10 most common errors in a log
grep "ERROR" app.log | awk '{print $NF}' | sort | uniq -c | sort -rn | head -10

# Find all unique IP addresses that got 404s
awk '$9 == 404 {print $1}' access.log | sort -u

# CSV: average of column 3 where column 1 equals "active"
awk -F',' '$1=="active" {sum+=$3; n++} END {print sum/n}' data.csv

# List all Python imports used across a project
grep -rh "^import\|^from" --include="*.py" . | sort -u

# Count lines of code per file type
find . -name "*.py" | xargs wc -l | sort -rn | head -20
```
