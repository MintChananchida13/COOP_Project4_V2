CREATE TABLE "embedding_jobs" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "template_id" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'queued',
    "requested_at" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "started_at" DATETIME,
    "completed_at" DATETIME,
    "error_message" TEXT,
    "vector_id" TEXT,
    "metadata_json" TEXT,
    CONSTRAINT "embedding_jobs_template_id_fkey" FOREIGN KEY ("template_id") REFERENCES "templates" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE INDEX "embedding_jobs_template_id_requested_at_idx" ON "embedding_jobs"("template_id", "requested_at");
