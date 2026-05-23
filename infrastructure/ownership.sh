#!/bin/bash
# Reset ownership of the data + service directories after a container that
# runs as root has touched them. PUID/PGID is 1000:1000 across the stack.

set -e

OWNER="${OWNER:-1000:1000}"
USER_HOME="/home/${SUDO_USER:-$USER}"

TARGETS=(
    "/mnt/storage"
    "${USER_HOME}/docker/compose"
    "${USER_HOME}/monitor"
    "${USER_HOME}/automation"
    "${USER_HOME}/documentation"
    "${USER_HOME}/portfolio"
)

for t in "${TARGETS[@]}"; do
    [[ -d "$t" ]] || continue
    sudo chown -R "$OWNER" "$t"
done
