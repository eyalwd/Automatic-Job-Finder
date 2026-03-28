import sqlite3
from datetime import date

DB_PATH = "jobs.db"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    url           TEXT UNIQUE NOT NULL,
    title         TEXT,
    company       TEXT,
    source        TEXT,
    description   TEXT,
    date_found    TEXT,
    cv_score      INTEGER DEFAULT NULL,
    job_score     INTEGER DEFAULT NULL,
    rationale_cv  TEXT,
    rationale_job TEXT,
    status        TEXT DEFAULT 'new'
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    # Migration: add columns that may be missing from older DB files
    for col, definition in [("source", "TEXT"), ("rationale_cv", "TEXT"), ("rationale_job", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {definition}")
        except Exception:
            pass  # column already exists
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    """Delete all rows and reset the auto-increment counter."""
    conn.execute("DELETE FROM jobs")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='jobs'")
    conn.commit()
    print("Database reset — all jobs deleted.")


def job_exists(conn: sqlite3.Connection, url: str) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE url = ?", (url,)).fetchone()
    return row is not None


def insert_job(conn: sqlite3.Connection, title: str, company: str, url: str,
               description: str, source: str = "") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO jobs (url, title, company, source, description, date_found) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (url, title, company, source, description, date.today().isoformat()),
    )
    conn.commit()


def update_match(
    conn: sqlite3.Connection,
    url: str,
    cv_score: int,
    job_score: int,
    rationale_cv: str,
    rationale_job: str,
) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET cv_score = ?, job_score = ?, rationale_cv = ?, rationale_job = ?
        WHERE url = ?
        """,
        (cv_score, job_score, rationale_cv, rationale_job, url),
    )
    conn.commit()


def get_new_matches(conn: sqlite3.Connection, threshold: int = 75) -> list[dict]:
    rows = conn.execute(
        """
        SELECT title, company, source, url, cv_score, job_score, rationale_cv, rationale_job
        FROM jobs
        WHERE (job_score >= ? OR cv_score >= ?) AND status = 'new'
        ORDER BY job_score DESC
        """,
        (threshold, threshold),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_notified(conn: sqlite3.Connection, url: str) -> None:
    conn.execute("UPDATE jobs SET status = 'notified' WHERE url = ?", (url,))
    conn.commit()


def reset_all_status(conn: sqlite3.Connection) -> int:
    cursor = conn.execute("UPDATE jobs SET status = 'new'")
    conn.commit()
    return cursor.rowcount


if __name__ == "__main__":
    import sys
    conn = get_connection()
    init_db(conn)

    if "--reset" in sys.argv:
        reset_db(conn)
    else:
        # Smoke test
        print(f"Database initialized at {DB_PATH}")
        insert_job(conn, "Algorithm Engineer", "Test Corp",
                   "https://example.com/job/1", "Test description.", source="Test")
        update_match(conn, "https://example.com/job/1", cv_score=88, job_score=82,
                     rationale_cv="Strong signal processing background matches requirements.",
                     rationale_job="Competitive profile but limited commercial software experience.")
        matches = get_new_matches(conn)
        print(f"New matches (score >= 75): {len(matches)}")
        for m in matches:
            print(f"  [{m['job_score']}] {m['title']} @ {m['company']} [{m['source']}]")

    conn.close()
