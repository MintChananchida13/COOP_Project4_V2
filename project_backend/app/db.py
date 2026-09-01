import os
import re
from urllib.parse import urlparse
from typing import Any, Dict, List, Optional, Sequence

from .auth_password import hash_password


_POSTGRES_READY = False
_SEED_USERS = [
    {
        "id": "usr_seed_user",
        "email": "user@ocr.com",
        "password": "user123",
        "role": "user",
    },
    {
        "id": "usr_seed_admin",
        "email": "admin@ocr.com",
        "password": "admin123",
        "role": "admin",
    },
]


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip().strip('"')


def is_postgres_enabled() -> bool:
    database_url = _database_url().lower()
    return database_url.startswith("postgresql://") or database_url.startswith("postgres://")


def database_target_summary() -> Dict[str, Optional[str]]:
    parsed = urlparse(_database_url())
    return {
        "scheme": parsed.scheme or None,
        "host": parsed.hostname,
        "port": str(parsed.port) if parsed.port else None,
        "database": parsed.path.lstrip("/") or None,
    }


class StaticCursor:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        self._rows = rows or []

    def fetchone(self) -> Optional[Dict[str, Any]]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> List[Dict[str, Any]]:
        return self._rows


class PostgresConnection:
    def __init__(self, raw_conn: Any):
        self._raw_conn = raw_conn

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        normalized = sql.strip()
        lowered = normalized.lower()
        if lowered.startswith("pragma foreign_keys"):
            return StaticCursor()
        if lowered.startswith("pragma table_info"):
            return self._table_info(normalized)

        translated_sql = _translate_sql(normalized)
        cursor = self._raw_conn.cursor()
        cursor.execute(translated_sql, tuple(params or ()))
        return cursor

    def commit(self) -> None:
        self._raw_conn.commit()

    def rollback(self) -> None:
        self._raw_conn.rollback()

    def close(self) -> None:
        self._raw_conn.close()

    def _table_info(self, sql: str) -> StaticCursor:
        match = re.search(r"pragma\s+table_info\((?:\"|')?([^\"')]+)(?:\"|')?\)", sql, re.IGNORECASE)
        table_name = match.group(1) if match else ""
        cursor = self._raw_conn.cursor()
        cursor.execute(
            """
            SELECT column_name AS name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        return StaticCursor([dict(row) for row in cursor.fetchall()])


def _translate_sql(sql: str) -> str:
    sql = sql.replace("DATETIME", "TIMESTAMPTZ")
    sql = sql.replace(" rowid ", " id ")
    sql = sql.replace(", rowid ", ", id ")
    return sql.replace("?", "%s")


def _connect_postgres() -> PostgresConnection:
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL mode requires psycopg2-binary. Install backend requirements first."
        ) from exc

    conn = psycopg2.connect(_database_url(), cursor_factory=psycopg2.extras.RealDictCursor)
    wrapped = PostgresConnection(conn)
    _ensure_postgres_schema(wrapped)
    return wrapped


def connect() -> Any:
    if not is_postgres_enabled():
        raise RuntimeError(
            "PostgreSQL is required. Set DATABASE_URL to a postgresql:// or postgres:// connection string."
        )
    return _connect_postgres()


def _ensure_postgres_schema(conn: PostgresConnection) -> None:
    global _POSTGRES_READY
    if _POSTGRES_READY:
        return

    for statement in _POSTGRES_SCHEMA:
        conn.execute(statement)
    conn.commit()
    _POSTGRES_READY = True


def _seed_default_users(conn: PostgresConnection) -> int:
    inserted = 0
    for user in _SEED_USERS:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (user["email"],)).fetchone()
        if existing is not None:
            continue
        conn.execute(
            """
            INSERT INTO users (id, email, password_hash, role, created_at, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (user["id"], user["email"], hash_password(user["password"]), user["role"]),
        )
        inserted += 1
    return inserted


def _log_seed_user_summary(conn: PostgresConnection, inserted: int) -> None:
    rows = conn.execute(
        """
        SELECT email, role
        FROM users
        WHERE email IN (?, ?)
        ORDER BY email ASC
        """,
        ("admin@ocr.com", "user@ocr.com"),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
    target = database_target_summary()
    seed_emails = [row["email"] for row in rows]
    print(
        "Database startup seed users checked "
        f"(target={target['host']}/{target['database']}, total_users={total['count'] if total else 0}, "
        f"seed_users_present={len(seed_emails)}/2, inserted={inserted}, emails={seed_emails})."
    )


def ensure_database_ready() -> Dict[str, Any]:
    with connect() as conn:
        inserted = _seed_default_users(conn)
        conn.commit()
        rows = conn.execute(
            """
            SELECT id, email, role
            FROM users
            WHERE email IN (?, ?)
            ORDER BY email ASC
            """,
            ("admin@ocr.com", "user@ocr.com"),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
    summary = {
        "database": database_target_summary(),
        "total_users": int(total["count"] if total else 0),
        "seed_users_present": len(rows),
        "seed_users": [{"id": row["id"], "email": row["email"], "role": row["role"]} for row in rows],
        "inserted": inserted,
    }
    print(
        "Database startup ready "
        f"(target={summary['database']['host']}/{summary['database']['database']}, "
        f"total_users={summary['total_users']}, seed_users_present={summary['seed_users_present']}/2, "
        f"inserted={inserted})."
    )
    return summary


_POSTGRES_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT NOT NULL PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS image_verification_categories (
        id TEXT NOT NULL PRIMARY KEY,
        value TEXT NOT NULL UNIQUE,
        label TEXT NOT NULL,
        prompt TEXT NOT NULL,
        match_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.70,
        margin_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.05,
        evidence_temperature DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS template_groups (
        id TEXT NOT NULL PRIMARY KEY,
        template_code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        document_type TEXT,
        category TEXT,
        description TEXT,
        created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS template_versions (
        id TEXT NOT NULL PRIMARY KEY,
        template_group_id TEXT NOT NULL REFERENCES template_groups(id) ON DELETE CASCADE,
        version_number INTEGER NOT NULL,
        version_name TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        detection_mode TEXT NOT NULL DEFAULT 'all_pages',
        main_page_number INTEGER NOT NULL DEFAULT 1,
        similarity_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.75,
        final_confidence_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.75,
        layout_weight DOUBLE PRECISION NOT NULL DEFAULT 0.50,
        text_anchor_weight DOUBLE PRECISION NOT NULL DEFAULT 0.35,
        image_anchor_weight DOUBLE PRECISION NOT NULL DEFAULT 0.15,
        created_from_version_id TEXT REFERENCES template_versions(id) ON DELETE SET NULL,
        created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        published_at TIMESTAMPTZ,
        CONSTRAINT template_versions_group_version_key UNIQUE (template_group_id, version_number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS template_pages (
        id TEXT NOT NULL PRIMARY KEY,
        template_version_id TEXT NOT NULL REFERENCES template_versions(id) ON DELETE CASCADE,
        page_number INTEGER NOT NULL,
        page_name TEXT,
        sample_image_url TEXT,
        normalized_image_url TEXT,
        layout_signature_json JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT template_pages_version_page_key UNIQUE (template_version_id, page_number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS extraction_fields (
        id TEXT NOT NULL PRIMARY KEY,
        template_page_id TEXT NOT NULL REFERENCES template_pages(id) ON DELETE CASCADE,
        field_name TEXT NOT NULL,
        display_label TEXT NOT NULL,
        data_type TEXT NOT NULL DEFAULT 'text',
        extraction_method TEXT NOT NULL DEFAULT 'fixed_roi',
        roi_x_ratio DOUBLE PRECISION NOT NULL,
        roi_y_ratio DOUBLE PRECISION NOT NULL,
        roi_width_ratio DOUBLE PRECISION NOT NULL,
        roi_height_ratio DOUBLE PRECISION NOT NULL,
        roi_mode TEXT NOT NULL DEFAULT 'fix',
        expected_content TEXT,
        required BOOLEAN NOT NULL DEFAULT FALSE,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT extraction_fields_data_type_check CHECK (data_type IN ('text', 'table', 'image')),
        CONSTRAINT extraction_fields_roi_mode_check CHECK (roi_mode IN ('fix', 'flexible')),
        CONSTRAINT extraction_fields_roi_ratio_check CHECK (
            roi_x_ratio >= 0 AND roi_x_ratio <= 1 AND
            roi_y_ratio >= 0 AND roi_y_ratio <= 1 AND
            roi_width_ratio > 0 AND roi_width_ratio <= 1 AND
            roi_height_ratio > 0 AND roi_height_ratio <= 1
        ),
        CONSTRAINT extraction_fields_page_field_name_key UNIQUE (template_page_id, field_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS verification_anchors (
        id TEXT NOT NULL PRIMARY KEY,
        template_page_id TEXT NOT NULL REFERENCES template_pages(id) ON DELETE CASCADE,
        anchor_name TEXT NOT NULL,
        anchor_type TEXT NOT NULL DEFAULT 'text',
        roi_x_ratio DOUBLE PRECISION NOT NULL,
        roi_y_ratio DOUBLE PRECISION NOT NULL,
        roi_width_ratio DOUBLE PRECISION NOT NULL,
        roi_height_ratio DOUBLE PRECISION NOT NULL,
        required BOOLEAN NOT NULL DEFAULT FALSE,
        weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
        expected_text TEXT,
        match_type TEXT,
        regex_pattern TEXT,
        image_category_id TEXT REFERENCES image_verification_categories(id) ON DELETE SET NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT verification_anchors_type_check CHECK (anchor_type IN ('text', 'image')),
        CONSTRAINT verification_anchors_roi_ratio_check CHECK (
            roi_x_ratio >= 0 AND roi_x_ratio <= 1 AND
            roi_y_ratio >= 0 AND roi_y_ratio <= 1 AND
            roi_width_ratio > 0 AND roi_width_ratio <= 1 AND
            roi_height_ratio > 0 AND roi_height_ratio <= 1
        ),
        CONSTRAINT verification_anchors_page_anchor_name_key UNIQUE (template_page_id, anchor_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ignore_regions (
        id TEXT NOT NULL PRIMARY KEY,
        template_page_id TEXT NOT NULL REFERENCES template_pages(id) ON DELETE CASCADE,
        region_name TEXT NOT NULL,
        roi_x_ratio DOUBLE PRECISION NOT NULL,
        roi_y_ratio DOUBLE PRECISION NOT NULL,
        roi_width_ratio DOUBLE PRECISION NOT NULL,
        roi_height_ratio DOUBLE PRECISION NOT NULL,
        reason TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT ignore_regions_roi_ratio_check CHECK (
            roi_x_ratio >= 0 AND roi_x_ratio <= 1 AND
            roi_y_ratio >= 0 AND roi_y_ratio <= 1 AND
            roi_width_ratio > 0 AND roi_width_ratio <= 1 AND
            roi_height_ratio > 0 AND roi_height_ratio <= 1
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS template_requests (
        id TEXT NOT NULL PRIMARY KEY,
        requested_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        request_title TEXT NOT NULL,
        document_type TEXT,
        request_mode TEXT NOT NULL DEFAULT 'image_only',
        status TEXT NOT NULL DEFAULT 'draft',
        user_note TEXT,
        admin_note TEXT,
        converted_template_group_id TEXT REFERENCES template_groups(id) ON DELETE SET NULL,
        converted_template_version_id TEXT REFERENCES template_versions(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        reviewed_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS template_request_pages (
        id TEXT NOT NULL PRIMARY KEY,
        template_request_id TEXT NOT NULL REFERENCES template_requests(id) ON DELETE CASCADE,
        page_number INTEGER NOT NULL,
        page_name TEXT,
        sample_image_url TEXT,
        source_file_id TEXT,
        source_file_name TEXT,
        image_source TEXT NOT NULL DEFAULT 'user_request',
        review_status TEXT NOT NULL DEFAULT 'pending',
        is_canonical BOOLEAN NOT NULL DEFAULT FALSE,
        layout_signature_json JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT template_request_pages_request_page_key UNIQUE (template_request_id, page_number)
    )
    """,
    "ALTER TABLE template_request_pages ADD COLUMN IF NOT EXISTS source_file_id TEXT",
    "ALTER TABLE template_request_pages ADD COLUMN IF NOT EXISTS image_source TEXT NOT NULL DEFAULT 'user_request'",
    "ALTER TABLE template_request_pages ADD COLUMN IF NOT EXISTS is_canonical BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE template_request_pages ADD COLUMN IF NOT EXISTS layout_signature_json JSONB",
    "ALTER TABLE template_request_pages ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP",
    """
    CREATE TABLE IF NOT EXISTS version_test_cases (
        id TEXT NOT NULL PRIMARY KEY,
        template_version_id TEXT NOT NULL REFERENCES template_versions(id) ON DELETE CASCADE,
        test_name TEXT NOT NULL,
        page_number INTEGER NOT NULL,
        image_url TEXT NOT NULL,
        expected_match BOOLEAN NOT NULL DEFAULT TRUE,
        test_type TEXT NOT NULL DEFAULT 'pre_publish',
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS requested_fields (
        id TEXT NOT NULL PRIMARY KEY,
        template_request_page_id TEXT NOT NULL REFERENCES template_request_pages(id) ON DELETE CASCADE,
        field_name TEXT NOT NULL,
        display_label TEXT NOT NULL,
        data_type TEXT NOT NULL DEFAULT 'text',
        extraction_method TEXT NOT NULL DEFAULT 'ocr_text',
        roi_x_ratio DOUBLE PRECISION NOT NULL,
        roi_y_ratio DOUBLE PRECISION NOT NULL,
        roi_width_ratio DOUBLE PRECISION NOT NULL,
        roi_height_ratio DOUBLE PRECISION NOT NULL,
        user_note TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT requested_fields_roi_ratio_check CHECK (
            roi_x_ratio >= 0 AND roi_x_ratio <= 1 AND
            roi_y_ratio >= 0 AND roi_y_ratio <= 1 AND
            roi_width_ratio > 0 AND roi_width_ratio <= 1 AND
            roi_height_ratio > 0 AND roi_height_ratio <= 1
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS publish_jobs (
        id TEXT NOT NULL PRIMARY KEY,
        template_version_id TEXT NOT NULL REFERENCES template_versions(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'queued',
        step TEXT NOT NULL DEFAULT 'validation',
        error_message TEXT,
        metadata_json JSONB,
        requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ocr_jobs (
        id TEXT NOT NULL PRIMARY KEY,
        requested_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        template_version_id TEXT REFERENCES template_versions(id) ON DELETE SET NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        request_json JSONB NOT NULL,
        result_json JSONB,
        error_message TEXT,
        requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ
    )
    """,
    'CREATE INDEX IF NOT EXISTS template_groups_document_type_idx ON template_groups(document_type)',
    'CREATE INDEX IF NOT EXISTS template_versions_group_status_idx ON template_versions(template_group_id, status)',
    'CREATE INDEX IF NOT EXISTS template_versions_status_updated_at_idx ON template_versions(status, updated_at)',
    'CREATE INDEX IF NOT EXISTS template_pages_version_page_idx ON template_pages(template_version_id, page_number)',
    'CREATE INDEX IF NOT EXISTS extraction_fields_template_page_id_sort_order_idx ON extraction_fields(template_page_id, sort_order)',
    'CREATE INDEX IF NOT EXISTS verification_anchors_template_page_id_sort_order_idx ON verification_anchors(template_page_id, sort_order)',
    'CREATE INDEX IF NOT EXISTS ignore_regions_template_page_id_idx ON ignore_regions(template_page_id)',
    'CREATE INDEX IF NOT EXISTS template_requests_status_created_at_idx ON template_requests(status, created_at)',
    'CREATE INDEX IF NOT EXISTS template_request_pages_request_page_idx ON template_request_pages(template_request_id, page_number)',
    'CREATE INDEX IF NOT EXISTS requested_fields_template_request_page_id_idx ON requested_fields(template_request_page_id)',
    'CREATE INDEX IF NOT EXISTS version_test_cases_template_version_id_idx ON version_test_cases(template_version_id)',
    'CREATE INDEX IF NOT EXISTS publish_jobs_template_version_step_idx ON publish_jobs(template_version_id, step, status)',
    'CREATE INDEX IF NOT EXISTS ocr_jobs_status_requested_at_idx ON ocr_jobs(status, requested_at)',
    'CREATE INDEX IF NOT EXISTS ocr_jobs_template_version_id_idx ON ocr_jobs(template_version_id)',
    """
    CREATE OR REPLACE VIEW template_versions_view AS
    SELECT
        tv.id AS template_version_id,
        tg.id AS template_group_id,
        tg.name AS template_name,
        tg.template_code,
        tg.document_type,
        tg.category,
        tv.version_number,
        tv.version_name,
        tv.status,
        tv.detection_mode,
        tv.main_page_number,
        tv.final_confidence_threshold,
        tv.similarity_threshold,
        tv.layout_weight,
        tv.text_anchor_weight,
        tv.image_anchor_weight,
        tv.published_at,
        tv.created_at,
        tv.updated_at
    FROM template_versions tv
    JOIN template_groups tg ON tg.id = tv.template_group_id
    """,
    """
    CREATE OR REPLACE VIEW template_fields_view AS
    SELECT
        tv.id AS template_version_id,
        tg.name AS template_name,
        tv.version_number,
        tv.version_name,
        tv.status,
        tp.id AS template_page_id,
        tp.page_number,
        tp.page_name,
        ef.id AS field_id,
        ef.field_name,
        ef.display_label,
        ef.data_type,
        ef.extraction_method,
        ef.roi_mode,
        ef.required,
        ef.sort_order,
        ef.created_at,
        ef.updated_at
    FROM extraction_fields ef
    JOIN template_pages tp ON tp.id = ef.template_page_id
    JOIN template_versions tv ON tv.id = tp.template_version_id
    JOIN template_groups tg ON tg.id = tv.template_group_id
    """,
    """
    CREATE OR REPLACE VIEW verification_anchors_view AS
    SELECT
        tv.id AS template_version_id,
        tg.name AS template_name,
        tv.version_number,
        tv.version_name,
        tv.status,
        tp.id AS template_page_id,
        tp.page_number,
        tp.page_name,
        va.id AS anchor_id,
        va.anchor_name,
        va.anchor_type,
        va.required,
        va.weight,
        va.sort_order,
        va.created_at,
        va.updated_at,
        va.image_category_id,
        ivc.value AS image_category_value,
        ivc.label AS image_category_label
    FROM verification_anchors va
    JOIN template_pages tp ON tp.id = va.template_page_id
    JOIN template_versions tv ON tv.id = tp.template_version_id
    JOIN template_groups tg ON tg.id = tv.template_group_id
    LEFT JOIN image_verification_categories ivc ON ivc.id = va.image_category_id
    """,
]
