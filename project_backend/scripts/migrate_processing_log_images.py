"""Backfill legacy Processing Log images into PostgreSQL. Dry-run by default."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.local_env import load_local_env
load_local_env()
from app.business.services import ProcessingLogService, _connect
from app.core.db import ensure_database_ready


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-id', help='Only inspect/migrate this Log')
    parser.add_argument('--apply', action='store_true', help='Save available images to PostgreSQL; retain local originals')
    args = parser.parse_args()
    ensure_database_ready()
    service = ProcessingLogService()
    if args.log_id:
        ids = [args.log_id]
    else:
        with _connect() as conn:
            ids = [str(row['id']) for row in conn.execute('SELECT id FROM processing_logs ORDER BY id').fetchall()]
    for log_id in ids:
        print(json.dumps(service.migrate_log_images(log_id, dry_run=not args.apply), ensure_ascii=True))


if __name__ == '__main__':
    run()
