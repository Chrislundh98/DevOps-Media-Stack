# Python Virtual Environments

Isolate your project dependencies so they don't conflict with each other or the system Python.

---

## Creating & Using a venv

```bash
# Create
python3 -m venv venv

# Activate
source venv/bin/activate         # Linux/Mac
# Your prompt changes to: (venv) user@host:~$

# Deactivate (when done)
deactivate
```

---

## Managing Packages

```bash
# Install packages
pip install requests beautifulsoup4 discord.py

# Install from requirements file
pip install -r requirements.txt

# Freeze current packages to file
pip freeze > requirements.txt

# Upgrade a package
pip install --upgrade requests

# Uninstall
pip uninstall package_name

# List installed packages
pip list

# Show package details
pip show requests
```

---

## requirements.txt

**Create it (freeze current environment):**
```bash
pip freeze > requirements.txt
```

**Example requirements.txt:**
```
requests==2.31.0
beautifulsoup4==4.12.3
discord.py==2.3.2
python-dotenv==1.0.1
qbittorrent-api==2024.2.59
```

**Install from it:**
```bash
pip install -r requirements.txt
```

**Tip:** Pin versions (with `==`) for production/Docker. Use `>=` for development if you want latest.

---

## venv in Docker

You generally DON'T need a venv inside Docker — the container IS the isolation. Just install directly:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
```

If you DO use a venv in Docker (e.g., for NAS scripts that run outside containers), make sure the Dockerfile activates it:

```dockerfile
RUN python -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
RUN pip install -r requirements.txt
```

---

## venv for NAS Scripts (Non-Docker)

For scripts that run directly on the NAS (not in Docker):

```bash
cd /volume1/automation/trackers
python3 -m venv venv
source venv/bin/activate
pip install -r docker/requirements.txt
deactivate
```

Your run scripts should activate the venv:
```bash
#!/bin/bash
cd /volume1/automation/trackers
source venv/bin/activate
python3 -m core.torrentleech
deactivate
```

---

## Common Issues

**"externally-managed-environment" error (newer systems):**
```bash
pip install --break-system-packages package_name
# OR just use a venv (the proper fix)
```

**Wrong Python version:**
```bash
python3 --version                # Check version
python3.11 -m venv venv         # Use specific version
```

**venv not found:**
```bash
sudo apt install python3-venv    # Install venv module
```
