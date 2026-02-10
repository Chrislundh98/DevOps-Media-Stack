# Debugging Docker Containers

When things don't work, this is how you figure out why.

---

## Step 1: Check If It's Running

```bash
docker ps                        # Is the container listed?
docker ps -a                     # Is it stopped/crashed? (check STATUS column)
```

Status meanings:
- `Up 2 hours` — running fine
- `Exited (0)` — stopped cleanly
- `Exited (1)` — crashed with error
- `Restarting` — crash loop (keeps restarting and failing)

---

## Step 2: Read the Logs

```bash
docker logs container_name                 # Full log
docker logs --tail 50 container_name       # Last 50 lines
docker logs -f container_name              # Follow live

# For compose
docker compose logs service_name
docker compose logs -f service_name
```

**Common errors in logs:**
- `ModuleNotFoundError` → Missing Python package in Dockerfile/requirements.txt
- `Permission denied` → File ownership issue (try chown fix)
- `Connection refused` → Service it depends on isn't ready or wrong hostname/port
- `Address already in use` → Port conflict with another container or host process

---

## Step 3: Get Inside the Container

```bash
docker exec -it container_name bash
# If bash isn't available:
docker exec -it container_name /bin/sh
```

Once inside:
```bash
ls /app/                         # Check if files are mounted
cat /app/.env                    # Check if env vars file is there
env | grep DISCORD               # Check environment variables
python -c "import requests"      # Check if Python packages are installed
ping other_container             # Check network connectivity
curl http://other_service:8080   # Check if another service responds
```

---

## Step 4: Inspect Container Config

```bash
# Full details (mounts, network, env vars, everything)
docker inspect container_name

# Specific things
docker inspect container_name | grep -A5 "Mounts"
docker inspect container_name | grep -A5 "Env"
docker inspect --format='{{.NetworkSettings.IPAddress}}' container_name
docker inspect --format='{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' container_name
```

---

## Common Problems & Fixes

### Container keeps restarting (crash loop)
```bash
# Check what's happening
docker logs --tail 100 container_name

# Temporarily stop auto-restart to debug
docker update --restart=no container_name
docker stop container_name
docker start container_name
docker logs -f container_name
```

### Permission denied on mounted volumes
```bash
# Check what user the container runs as
docker exec container_name id

# Fix ownership on host
sudo chown -R 1000:1000 /volume1/automation/data/

# Or run container as specific user in compose:
# user: "1000:1000"
```

### Container can't connect to another container
```bash
# Check if they're on the same network
docker network inspect automation

# From inside the container, test connectivity
docker exec -it my-app ping database
docker exec -it my-app curl http://radarr:7878/api/v3/health
```

### Port conflict
```bash
# Find what's using a port
sudo lsof -i :8080
# Or
sudo ss -tlnp | grep 8080

# Check Docker port mappings
docker port container_name
```

### Out of disk space
```bash
docker system df                   # See Docker disk usage
docker system prune -a             # Clean everything unused
docker volume prune                # Clean unused volumes
```

### Image won't build
```bash
# Build with verbose output
docker build --progress=plain -t my-app .

# Build without cache (force fresh)
docker build --no-cache -t my-app .
```

---

## Live Monitoring

```bash
# Resource usage (CPU, RAM, net I/O) for all containers
docker stats

# Filter to specific containers
docker stats container1 container2

# One-shot (no live update)
docker stats --no-stream
```

---

## Quick Debugging Checklist

1. ☐ `docker ps -a` — Is it running? What's the exit code?
2. ☐ `docker logs --tail 100 container` — What do the logs say?
3. ☐ `docker exec -it container bash` — Can you get a shell?
4. ☐ `docker inspect container` — Are volumes and env vars correct?
5. ☐ `docker network inspect network` — Are containers on the same network?
6. ☐ `docker system df` — Is there enough disk space?
