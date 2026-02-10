# Networking Basics

Common networking commands and concepts for server/NAS management.

---

## IP & Interface Info

```bash
ip addr                          # All interfaces and IPs
ip addr show eth0                # Specific interface
hostname -I                      # Just the IP addresses
curl ifconfig.me                 # Your public IP
```

---

## Connectivity Testing

```bash
ping 192.168.0.1                 # Test if host is reachable (Ctrl+C to stop)
ping -c 4 google.com             # Send 4 pings then stop

traceroute google.com            # Trace the route to a host
mtr google.com                   # Live traceroute (if installed)
```

---

## DNS

```bash
nslookup google.com              # DNS lookup
dig google.com                   # Detailed DNS lookup
dig +short google.com            # Just the IP
host google.com                  # Simple lookup
```

---

## Ports & Connections

```bash
# What's listening on what port
ss -tlnp                         # TCP listeners with process names
ss -ulnp                         # UDP listeners
sudo lsof -i :8080               # What's using port 8080

# Test if a port is open
nc -zv 192.168.0.80 8080         # Quick port check
curl -s http://192.168.0.80:7878 # Test HTTP service

# All active connections
ss -tnp                          # TCP connections with processes
netstat -tnp                     # Alternative (older tool)
```

---

## Common Port Reference

| Port | Service |
|------|---------|
| 22 | SSH |
| 80 | HTTP |
| 443 | HTTPS |
| 3000 | Grafana / dev servers |
| 6767 | Bazarr |
| 7878 | Radarr |
| 8080 | qBittorrent WebUI |
| 8096 | Jellyfin |
| 8265 | Tdarr |
| 8989 | Sonarr |
| 9696 | Prowlarr |

---

## Downloading

```bash
# Download a file
wget https://example.com/file.tar.gz
curl -O https://example.com/file.tar.gz

# Download with custom filename
wget -O myfile.tar.gz https://example.com/file.tar.gz
curl -o myfile.tar.gz https://example.com/file.tar.gz

# API request (GET)
curl -s https://api.example.com/data | python3 -m json.tool

# API request (POST with JSON)
curl -X POST https://api.example.com/data \
  -H "Content-Type: application/json" \
  -d '{"key": "value"}'

# Follow redirects
curl -L https://short.url/abc
```

---

## Firewall (ufw)

```bash
sudo ufw status                  # Check firewall status
sudo ufw allow 22/tcp            # Allow SSH
sudo ufw allow 8080/tcp          # Allow qBittorrent
sudo ufw enable                  # Turn on firewall
sudo ufw deny 3000/tcp           # Block a port
sudo ufw delete allow 3000/tcp   # Remove a rule
```

---

## Bandwidth & Traffic

```bash
iftop                            # Live bandwidth per connection
nethogs                          # Bandwidth per process
vnstat                           # Traffic statistics over time

# Quick speed test
curl -s https://raw.githubusercontent.com/sivel/speedtest-cli/master/speedtest.py | python3
```
