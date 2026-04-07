"""Create a minimal Things 3 SQLite fixture database for testing.

Generates tests/fixtures/things_fixture.sqlite with the schema and seed data
needed to test reads.py against a real (but controlled) database via the
THINGSDB environment variable.

Run:
    python tests/fixtures/create_fixture.py
"""

from __future__ import annotations

import os
import plistlib
import sqlite3
import time
from datetime import date, datetime, timedelta

# Output path (relative to repo root)
FIXTURE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(FIXTURE_DIR, "things_fixture.sqlite")


def things_date(d: date) -> int:
    """Encode a Python date into Things' binary date format.

    Format: YYYYYYYYYYYMMMMDDDDD0000000 (year << 16 | month << 12 | day << 7)
    """
    return (d.year << 16) | (d.month << 12) | (d.day << 7)


def unix_timestamp(dt: datetime) -> float:
    """Convert a datetime to Unix timestamp for Things' REAL date columns."""
    return dt.timestamp()


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the minimal Things 3 schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS Meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS TMTask (
            uuid TEXT PRIMARY KEY,
            type INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL DEFAULT '',
            status INTEGER NOT NULL DEFAULT 0,
            start INTEGER NOT NULL DEFAULT 1,
            startDate INTEGER,
            deadline INTEGER,
            notes TEXT,
            project TEXT,
            area TEXT,
            heading TEXT,
            trashed INTEGER NOT NULL DEFAULT 0,
            "index" INTEGER NOT NULL DEFAULT 0,
            todayIndex INTEGER NOT NULL DEFAULT 0,
            creationDate REAL,
            userModificationDate REAL,
            stopDate REAL,
            rt1_recurrenceRule TEXT,
            deadlineSuppressionDate TEXT,
            evening INTEGER NOT NULL DEFAULT 0,
            reminderTime INTEGER
        );

        CREATE TABLE IF NOT EXISTS TMArea (
            uuid TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            "index" INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS TMTag (
            uuid TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            "index" INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS TMTaskTag (
            tasks TEXT NOT NULL,
            tags TEXT NOT NULL,
            PRIMARY KEY (tasks, tags)
        );

        CREATE TABLE IF NOT EXISTS TMChecklistItem (
            uuid TEXT PRIMARY KEY,
            task TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            status INTEGER NOT NULL DEFAULT 0,
            "index" INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS TMSettings (
            uuid TEXT PRIMARY KEY,
            uriSchemeAuthenticationToken TEXT
        );
    """)


def seed_meta(conn: sqlite3.Connection) -> None:
    """Insert Meta row with databaseVersion > 21 (plist-encoded)."""
    version_plist = plistlib.dumps(22, fmt=plistlib.FMT_XML).decode()
    conn.execute(
        "INSERT INTO Meta (key, value) VALUES (?, ?)",
        ("databaseVersion", version_plist),
    )


def seed_data(conn: sqlite3.Connection) -> None:
    """Insert known test items covering all derived_list states."""
    today = date.today()
    tomorrow = today + timedelta(days=7)
    yesterday = today - timedelta(days=1)
    now = datetime.now()
    created_ts = unix_timestamp(now - timedelta(hours=1))
    modified_ts = unix_timestamp(now)

    # type: 0=to-do, 1=project, 2=heading
    # status: 0=incomplete, 2=canceled, 3=completed
    # start: 0=Inbox, 1=Anytime, 2=Someday

    tasks = [
        # (uuid, type, title, status, start, startDate, deadline, notes,
        #  project, area, heading, trashed, index, todayIndex, created, modified, stopDate, evening)
        (
            "InboxTask00000000000001", 0, "Buy groceries", 0, 0,
            None, None, "Milk, eggs, bread",
            None, None, None, 0, 0, 0, created_ts, modified_ts, None, 0,
        ),
        (
            "TodayTask00000000000001", 0, "Review pull request", 0, 1,
            things_date(today), None, "Check the new feature branch",
            None, None, None, 0, 1, 0, created_ts, modified_ts, None, 0,
        ),
        (
            "UpcomingTask000000000001", 0, "Dentist appointment", 0, 1,
            things_date(tomorrow), None, None,
            None, None, None, 0, 2, 0, created_ts, modified_ts, None, 0,
        ),
        (
            "AnytimeTask000000000001", 0, "Read Designing Data Apps", 0, 1,
            None, None, "Chapter 5 onwards",
            None, None, None, 0, 3, 0, created_ts, modified_ts, None, 0,
        ),
        (
            "SomedayTask000000000001", 0, "Learn Rust", 0, 2,
            None, None, None,
            None, None, None, 0, 4, 0, created_ts, modified_ts, None, 0,
        ),
        (
            "CompletedTask0000000001", 0, "Ship v1.0", 3, 1,
            None, None, "Done!",
            None, None, None, 0, 5, 0, created_ts, modified_ts,
            unix_timestamp(now - timedelta(minutes=30)), 0,
        ),
        (
            "CanceledTask00000000001", 0, "Old migration script", 2, 1,
            None, None, None,
            None, None, None, 0, 6, 0, created_ts, modified_ts,
            unix_timestamp(now - timedelta(minutes=15)), 0,
        ),
        # A project
        (
            "ProjectTask000000000001", 1, "Website Redesign", 0, 1,
            None, things_date(today + timedelta(days=30)), "Major overhaul",
            None, "AreaWork0000000000000001", None, 0, 7, 0,
            created_ts, modified_ts, None, 0,
        ),
        # A to-do inside the project
        (
            "ChildTask0000000000001a", 0, "Design mockups", 0, 1,
            None, None, None,
            "ProjectTask000000000001", "AreaWork0000000000000001", None, 0, 0, 0,
            created_ts, modified_ts, None, 0,
        ),
        # An evening task (Today + evening flag)
        (
            "EveningTask000000000001", 0, "Evening meditation", 0, 1,
            things_date(today), None, None,
            None, None, None, 0, 8, 1, created_ts, modified_ts, None, 1,
        ),
        # A task with a deadline
        (
            "DeadlineTask00000000001", 0, "Tax filing", 0, 1,
            None, things_date(today + timedelta(days=14)), "Gather documents",
            None, None, None, 0, 9, 0, created_ts, modified_ts, None, 0,
        ),
    ]

    conn.executemany(
        """INSERT INTO TMTask
            (uuid, type, title, status, start, startDate, deadline, notes,
             project, area, heading, trashed, "index", todayIndex,
             creationDate, userModificationDate, stopDate, evening)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        tasks,
    )

    # Areas
    conn.executemany(
        'INSERT INTO TMArea (uuid, title, "index") VALUES (?, ?, ?)',
        [
            ("AreaWork0000000000000001", "Work", 0),
            ("AreaPersonal00000000001a", "Personal", 1),
        ],
    )

    # Tags
    conn.executemany(
        'INSERT INTO TMTag (uuid, title, "index") VALUES (?, ?, ?)',
        [
            ("TagUrgent000000000000001", "urgent", 0),
            ("TagHome00000000000000001", "home", 1),
        ],
    )

    # Tag assignments
    conn.executemany(
        "INSERT INTO TMTaskTag (tasks, tags) VALUES (?, ?)",
        [
            ("InboxTask00000000000001", "TagHome00000000000000001"),
            ("TodayTask00000000000001", "TagUrgent000000000000001"),
            ("DeadlineTask00000000001", "TagUrgent000000000000001"),
        ],
    )

    # Checklist items
    conn.executemany(
        'INSERT INTO TMChecklistItem (uuid, task, title, status, "index") VALUES (?, ?, ?, ?, ?)',
        [
            ("CL000000000000000000001", "TodayTask00000000000001", "Check tests", 0, 0),
            ("CL000000000000000000002", "TodayTask00000000000001", "Review docs", 3, 1),
        ],
    )


def main() -> None:
    """Generate the fixture database."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    try:
        create_schema(conn)
        seed_meta(conn)
        seed_data(conn)
        conn.commit()
        print(f"Created fixture database: {DB_PATH}")

        # Verify
        version = conn.execute(
            "SELECT value FROM Meta WHERE key = 'databaseVersion'"
        ).fetchone()[0]
        task_count = conn.execute("SELECT COUNT(*) FROM TMTask").fetchone()[0]
        print(f"  databaseVersion plist present: True")
        print(f"  Tasks: {task_count}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
