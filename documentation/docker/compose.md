# Docker Compose

Managing multi-container setups with a single YAML file.

---

## Basic Commands

```bash
docker compose up -d                  # Start all services (detached)
docker compose down                   # Stop and remove all services
docker compose restart                # Restart all services
docker compose restart service_name   # Restart one service
docker compose stop                   # Stop without removing
docker compose start                  # Start stopped services
docker compose pull                   # Pull latest images for all services
```

> **Note:** `docker-compose` (with hyphen) is the old v1 syntax. `docker compose` (with space) is v2. Both work, but v2 is recommended.

---

## Logs

```bash
docker compose logs                   # All service logs
docker compose logs -f                # Follow all logs
docker compose logs -f service_name   # Follow one service
docker compose logs --tail 50         # Last 50 lines per service
```

---

## Status & Info

```bash
docker compose ps                     # List running services
docker compose top                    # Processes in each service
docker compose config                 # Validate and display the resolved config
```

---

## Update Workflow

The standard flow for updating containers:

```bash
# 1. Pull latest images
docker compose pull

# 2. Recreate with new images (only changed services restart)
docker compose up -d

# 3. Clean up old images
docker image prune
```

**One-liner:**
```bash
docker compose pull && docker compose up -d && docker image prune -f
```

---

## docker-compose.yml Basics

```yaml
services:
  my-app:
    image: python:3.11-slim          # Use pre-built image
    # OR build from Dockerfile:
    # build:
    #   context: .
    #   dockerfile: Dockerfile
    container_name: my-app
    restart: unless-stopped          # Auto-restart on failure/reboot
    ports:
      - "8080:80"                    # host:container
    volumes:
      - ./data:/app/data             # Bind mount
      - app-logs:/app/logs           # Named volume
    environment:
      - TZ=Europe/Stockholm
      - DEBUG=false
    env_file:
      - .env                         # Load variables from file
    networks:
      - automation
    depends_on:
      - database                     # Start after database

  database:
    image: postgres:16
    container_name: my-db
    restart: unless-stopped
    volumes:
      - db-data:/var/lib/postgresql/data
    environment:
      POSTGRES_PASSWORD: ${DB_PASS}  # From .env file
    networks:
      - automation

volumes:
  app-logs:
  db-data:

networks:
  automation:
    name: automation
```

---

## Environment Variables

**Three ways to pass env vars:**

**1. Inline in compose file:**
```yaml
environment:
  - DISCORD_WEBHOOK=https://example.com
  - TZ=Europe/Stockholm
```

**2. From .env file (recommended for secrets):**
```yaml
env_file:
  - .env
```

**3. Variable substitution (from host shell or .env in same dir):**
```yaml
environment:
  - DB_PASS=${DB_PASSWORD}     # Reads from host env or .env
```

Docker Compose automatically loads a `.env` file in the same directory as the compose file for variable substitution.

---

## Networking

By default, all services in a compose file can talk to each other using the service name as hostname.

```yaml
services:
  app:
    image: my-app
    networks:
      - frontend
      - backend

  database:
    image: postgres
    networks:
      - backend          # Only accessible from backend network

networks:
  frontend:
  backend:
```

```python
# From within the "app" container, connect to database:
db_host = "database"     # Service name = hostname
db_port = 5432
```

**External network (shared across compose files):**
```yaml
networks:
  automation:
    external: true       # Must already exist: docker network create automation
```

---

## Resource Limits

```yaml
services:
  lightweight-bot:
    image: python:3.11-slim
    deploy:
      resources:
        limits:
          memory: 128M
          cpus: '0.25'
        reservations:
          memory: 64M
```

---

## Useful Compose Patterns

**Run a one-off command:**
```bash
docker compose run --rm service_name python script.py
```

**Rebuild after Dockerfile changes:**
```bash
docker compose build                  # Rebuild all
docker compose build service_name     # Rebuild one
docker compose up -d --build          # Rebuild and start
```

**Scale a service:**
```bash
docker compose up -d --scale worker=3
```

**Override for development:**
Create `docker-compose.override.yml` (auto-loaded):
```yaml
services:
  app:
    environment:
      - DEBUG=true
    volumes:
      - ./src:/app/src    # Live code reload
```
