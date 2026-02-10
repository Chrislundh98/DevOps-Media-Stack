# Docker Essentials

Core Docker commands for managing containers, images, volumes, and networks.

---

## Container Lifecycle

```bash
# Start / stop / restart
docker start container_name
docker stop container_name
docker restart container_name

# Run a new container
docker run -d --name my-app -p 8080:80 nginx    # Detached, named, port mapped
docker run -it ubuntu bash                        # Interactive terminal

# Remove containers
docker rm container_name                          # Remove stopped container
docker rm -f container_name                       # Force remove (even if running)

# List containers
docker ps                                         # Running containers
docker ps -a                                      # All containers (including stopped)
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"  # Clean table
```

---

## Images

```bash
docker images                    # List local images
docker pull nginx:latest         # Download an image
docker rmi image_name            # Remove an image
docker image prune               # Remove dangling images
docker image prune -a            # Remove ALL unused images
```

---

## Logs

```bash
docker logs container_name                     # All logs
docker logs -f container_name                  # Follow (live tail)
docker logs --tail 50 container_name           # Last 50 lines
docker logs --since 1h container_name          # Last hour
docker logs -f --tail 20 container_name        # Follow, starting from last 20 lines
```

---

## Exec — Run Commands Inside Containers

```bash
docker exec -it container_name bash            # Shell into container
docker exec -it container_name /bin/sh         # If bash isn't available
docker exec container_name ls /app             # Run a single command
docker exec container_name cat /app/config.yml # View a file inside container
docker exec -it container_name python3         # Start Python REPL inside container
```

---

## Volumes

```bash
docker volume ls                               # List volumes
docker volume create my_data                   # Create volume
docker volume rm my_data                       # Remove volume
docker volume prune                            # Remove unused volumes
docker volume inspect my_data                  # Show volume details (mount path)
```

---

## Networks

```bash
docker network ls                              # List networks
docker network create my_network               # Create network
docker network connect my_network container    # Connect container to network
docker network disconnect my_network container # Disconnect
docker network inspect my_network              # Show details (connected containers)
```

---

## Inspection & Debugging

```bash
docker inspect container_name                  # Full container details (JSON)
docker inspect container_name | grep IPAddress # Get container IP
docker stats                                   # Live resource usage (CPU, RAM)
docker top container_name                      # Running processes in container
docker diff container_name                     # Files changed since container started
```

---

## Cleanup

```bash
# Remove stopped containers
docker container prune

# Remove unused images
docker image prune

# Remove unused volumes
docker volume prune

# THE NUCLEAR OPTION — removes everything unused
docker system prune -a --volumes
# ⚠️ This removes: stopped containers, unused networks, unused images, unused volumes

# See how much space Docker is using
docker system df
```

---

## Building Images

```bash
docker build -t my-app .                       # Build from Dockerfile in current dir
docker build -t my-app:v2 .                    # With tag
docker build -t my-app -f docker/Dockerfile .  # Custom Dockerfile path
docker build --no-cache -t my-app .            # Rebuild without cache
```

---

## Useful Patterns

**Restart all containers:**
```bash
docker restart $(docker ps -q)
```

**Stop all containers:**
```bash
docker stop $(docker ps -q)
```

**Get container name from partial match:**
```bash
docker ps --filter "name=torrent" --format "{{.Names}}"
```

**Copy files to/from container:**
```bash
docker cp file.txt container_name:/app/file.txt     # Local → container
docker cp container_name:/app/output.log ./          # Container → local
```

**Check container health:**
```bash
docker inspect --format='{{.State.Health.Status}}' container_name
```
