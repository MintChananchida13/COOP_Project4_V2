#!/bin/sh
set -eu

destination=/backup-output/database.dump
: "${NEON_DATABASE_URL:?Neon DATABASE_URL is required}"
if [ -e "$destination" ]; then
    echo "Database seed already exists. Move it before exporting again." >&2
    exit 1
fi

staging=$(mktemp /backup-output/.database.dump.XXXXXX)
trap 'rm -f "$staging"' EXIT
pg_dump --format=custom --no-password \
    --exclude-table-data=public.processing_logs \
    --exclude-table-data=public.processing_log_images \
    --file="$staging" --dbname="$NEON_DATABASE_URL"
pg_restore --list "$staging" >/dev/null
chgrp postgres "$staging"
chmod 0640 "$staging"
mv -n "$staging" "$destination"
if [ -e "$staging" ]; then
    echo "Database seed already exists." >&2
    exit 1
fi
echo "Exported database_seed/database.dump"
