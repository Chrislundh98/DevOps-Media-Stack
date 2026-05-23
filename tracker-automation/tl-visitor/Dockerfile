FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y \
    chromium \
    xvfb \
    x11vnc \
    python3 \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY entrypoint.sh visit.sh setup_cookies.py ./
RUN chmod +x entrypoint.sh visit.sh

VOLUME ["/profile"]
ENTRYPOINT ["/app/entrypoint.sh"]
