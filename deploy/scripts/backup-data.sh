#!/usr/bin/env sh
set -eu

SOURCE_DIR="${BACKUP_SOURCE:-./data}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

if [ ! -d "$SOURCE_DIR" ]; then
  echo "Backup source not found: $SOURCE_DIR" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$BACKUP_DIR/knowledge-hub-data-$STAMP.tar.gz"

tar -czf "$ARCHIVE" -C "$SOURCE_DIR" .
echo "Created backup: $ARCHIVE"

find "$BACKUP_DIR" -name "knowledge-hub-data-*.tar.gz" -type f -mtime +"$RETENTION_DAYS" -delete
echo "Deleted backups older than $RETENTION_DAYS days"
