#!/bin/sh
# Render mounts the persistent disk owned by root, but the app runs as the
# non-root user "appuser". So: start as root, make sure the data folder exists
# and belongs to appuser, then drop to appuser and start the app.
set -e

DATA_DIR="${DATA_DIR:-/app/data}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    chown -R appuser:appuser "$DATA_DIR"
    exec setpriv --reuid=appuser --regid=appuser --init-groups "$@"
fi

# Already running as a normal user (for example docker run --user): just start the app.
exec "$@"
