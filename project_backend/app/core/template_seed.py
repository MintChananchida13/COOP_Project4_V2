"""Move published templates between databases without exporting application history."""
import argparse
import base64
import json
import logging
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

import psycopg2
from PIL import Image

from app.core.local_env import load_local_env
from app.model_runtime.siglip_v23 import SINGLE
from app.processing.document_aspect import image_geometry


logger = logging.getLogger(__name__)
SEED_KEY = "template_seed_imported_v1"
PUBLISHED = "SELECT id FROM template_versions WHERE status IN ('active', 'published')"
PAGES = f"SELECT id FROM template_pages WHERE template_version_id IN ({PUBLISHED})"
TABLES = {
    "image_verification_categories": (
        f"id IN (SELECT image_category_id FROM verification_anchors WHERE template_page_id IN ({PAGES}))"
    ),
    "template_groups": f"id IN (SELECT template_group_id FROM template_versions WHERE id IN ({PUBLISHED}))",
    "template_versions": f"id IN ({PUBLISHED})",
    "template_pages": f"id IN ({PAGES})",
    "extraction_fields": f"template_page_id IN ({PAGES})",
    "verification_anchors": f"template_page_id IN ({PAGES})",
    "ignore_regions": f"template_page_id IN ({PAGES})",
}
IMAGE_FIELDS = ("sample_image_url", "normalized_image_url")


def validate_categories(rows) -> None:
    if any(row.get("value") not in SINGLE for row in rows):
        raise ValueError("Template seed references an image category unsupported by this backend")


def portable_image(source: str, page_id: str) -> str:
    if source.startswith("data:image/") and image_geometry(source):
        return source
    if not source.startswith(("http://", "https://", "data:")):
        path = Path(source)
        try:
            with Image.open(path) as image:
                mime = Image.MIME.get(image.format)
                image.verify()
            if mime and mime.startswith("image/"):
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                return f"data:{mime};base64,{encoded}"
        except (OSError, ValueError, Image.DecompressionBombError):
            pass
    raise ValueError(f"Template page {page_id} has an unreadable or remote image reference")


def export_seed(database_url: str, output: Path) -> int:
    if output.exists():
        raise FileExistsError(f"Seed already exists at {output}. Choose another output file.")
    connection = psycopg2.connect(database_url, connect_timeout=10)
    try:
        connection.set_session(isolation_level="REPEATABLE READ", readonly=True)
        with connection, connection.cursor() as cursor:
            tables = {}
            for table, condition in TABLES.items():
                cursor.execute(f"SELECT to_jsonb(t) FROM {table} AS t WHERE {condition} ORDER BY id")
                tables[table] = [record for (record,) in cursor]
    finally:
        connection.close()
    if not tables["template_versions"]:
        raise ValueError("No active or published templates are available to export")
    validate_categories(tables["image_verification_categories"])
    for row in tables["template_groups"] + tables["template_versions"]:
        row["created_by"] = None
    for row in tables["template_versions"]:
        row["created_from_version_id"] = None
    for row in tables["template_pages"]:
        if not any(row.get(key) for key in IMAGE_FIELDS):
            raise ValueError(f"Template page {row['id']} has no reference image")
        for key in IMAGE_FIELDS:
            if row.get(key):
                row[key] = portable_image(row[key], row["id"])

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"version": 1, "tables": tables}, handle, ensure_ascii=False, separators=(",", ":"))
        os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return len(tables["template_versions"])


def import_configured_seed(conn) -> None:
    configured = os.getenv("TEMPLATE_SEED_FILE")
    if not configured or not Path(configured).is_file():
        return
    if urlsplit(os.environ.get("DATABASE_URL", "")).hostname != "db":
        logger.warning("Template seed is enabled only for the local Compose database (host db)")
        return
    conn.execute("SELECT pg_advisory_xact_lock(276401708)")
    if conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (SEED_KEY,)).fetchone():
        return
    if conn.execute("SELECT 1 FROM template_groups LIMIT 1").fetchone():
        logger.warning("Template seed skipped because the database already has templates")
        return

    data = json.loads(Path(configured).read_text(encoding="utf-8"))
    tables = data.get("tables")
    if data.get("version") != 1 or not isinstance(tables, dict) or set(tables) != set(TABLES):
        raise ValueError("Unsupported template seed format")
    if any(not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) for rows in tables.values()):
        raise ValueError("Invalid template seed table rows")
    if not tables["template_versions"]:
        raise ValueError("Template seed contains no versions")
    validate_categories(tables["image_verification_categories"])
    for row in tables["template_versions"]:
        if row.get("status") not in {"active", "published"}:
            raise ValueError("Template seed must contain only active or published versions")
    for row in tables["template_pages"]:
        images = [row[key] for key in IMAGE_FIELDS if row.get(key)]
        if not images or any(not isinstance(image, str) or not image.startswith("data:image/") or not image_geometry(image) for image in images):
            raise ValueError(f"Invalid embedded image in template seed page {row.get('id')}")
    for table in TABLES:
        conflict = " ON CONFLICT (id) DO NOTHING" if table == "image_verification_categories" else ""
        conn.execute(
            f"INSERT INTO {table} SELECT * FROM jsonb_populate_recordset(NULL::{table}, ?::jsonb){conflict}",
            (json.dumps(tables[table], ensure_ascii=False),),
        )
    conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (SEED_KEY, "true"))
    logger.info("Imported %d active or published template versions", len(tables["template_versions"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("../template_seed/templates.json"))
    args = parser.parse_args()
    load_local_env()
    url = os.getenv("DATABASE_URL")
    if not url:
        parser.error("DATABASE_URL is required")
    try:
        count = export_seed(url, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Template export failed: {error}\n")
    except psycopg2.Error:
        parser.exit(1, "Template export failed: could not read templates from the configured PostgreSQL database.\n")
    print(f"Exported {count} template versions to {args.output}")


if __name__ == "__main__":
    main()
