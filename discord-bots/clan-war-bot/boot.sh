#! /bin/bash
docker compose down && docker compose build --no-cache && docker compose pull && docker compose up -d --remove-orphans