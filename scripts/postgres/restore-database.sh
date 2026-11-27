#!/bin/sh
set -eu

archive=/database_seed/database.dump
if [ -f "$archive" ]; then
    touch "$PGDATA/.ocr-restore-incomplete"
    echo "Restoring database seed into $POSTGRES_DB"
    pg_restore --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
        --no-owner --no-privileges --single-transaction "$archive"
    rm -f "$PGDATA/.ocr-restore-incomplete"
    echo "Database seed restored"
fi
