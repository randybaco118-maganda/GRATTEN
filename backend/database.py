import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 3


def db_path():
    # GRATTEN_DB lets tests (or deployments) point at a different file.
    return Path(os.environ.get("GRATTEN_DB") or ROOT / "instance" / "gratten.db")


def get_db():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Original (v1) schema, unchanged except users.role now allows 'Admin' for fresh databases.
BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    login_id TEXT NOT NULL UNIQUE,
    email TEXT UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('Student','Teacher','Admin')),
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    user_id INTEGER UNIQUE,
    name TEXT NOT NULL,
    course TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS teachers (
    teacher_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    name TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS subjects (
    subject_id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    units INTEGER NOT NULL DEFAULT 3,
    instructor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS teacher_subjects (
    teacher_id INTEGER NOT NULL,
    subject_id TEXT NOT NULL,
    PRIMARY KEY(teacher_id, subject_id),
    FOREIGN KEY(teacher_id) REFERENCES teachers(teacher_id) ON DELETE CASCADE,
    FOREIGN KEY(subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS enrollments (
    enrollment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    academic_year TEXT NOT NULL DEFAULT '2026-2027',
    semester TEXT NOT NULL DEFAULT 'First Semester',
    UNIQUE(student_id, subject_id, academic_year, semester),
    FOREIGN KEY(student_id) REFERENCES students(student_id) ON DELETE CASCADE,
    FOREIGN KEY(subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS grades (
    enrollment_id INTEGER PRIMARY KEY,
    quiz REAL NOT NULL DEFAULT 0 CHECK(quiz BETWEEN 0 AND 100),
    recitation REAL NOT NULL DEFAULT 0 CHECK(recitation BETWEEN 0 AND 100),
    output REAL NOT NULL DEFAULT 0 CHECK(output BETWEEN 0 AND 100),
    exam REAL NOT NULL DEFAULT 0 CHECK(exam BETWEEN 0 AND 100),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(enrollment_id) REFERENCES enrollments(enrollment_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS attendance (
    enrollment_id INTEGER PRIMARY KEY,
    present INTEGER NOT NULL DEFAULT 0 CHECK(present >= 0),
    late INTEGER NOT NULL DEFAULT 0 CHECK(late >= 0),
    absent INTEGER NOT NULL DEFAULT 0 CHECK(absent >= 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(enrollment_id) REFERENCES enrollments(enrollment_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id TEXT NOT NULL,
    teacher_id INTEGER,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('Output','Quiz','Exam')),
    max_points REAL NOT NULL CHECK(max_points > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE,
    FOREIGN KEY(teacher_id) REFERENCES teachers(teacher_id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS assessment_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id INTEGER NOT NULL,
    enrollment_id INTEGER NOT NULL,
    earned_points REAL NOT NULL CHECK(earned_points >= 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(assessment_id, enrollment_id),
    FOREIGN KEY(assessment_id) REFERENCES assessments(id) ON DELETE CASCADE,
    FOREIGN KEY(enrollment_id) REFERENCES enrollments(enrollment_id) ON DELETE CASCADE
);
"""

# v2: new tables only (all IF NOT EXISTS, nothing dropped).
V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS academic_years (
    academic_year_id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE,
    start_date TEXT, end_date TEXT,
    is_current INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS terms (
    term_id INTEGER PRIMARY KEY AUTOINCREMENT,
    academic_year_id INTEGER NOT NULL,
    semester TEXT NOT NULL CHECK(semester IN ('First Semester','Second Semester')),
    start_date TEXT, end_date TEXT,
    is_current INTEGER NOT NULL DEFAULT 0,
    UNIQUE(academic_year_id, semester),
    FOREIGN KEY(academic_year_id) REFERENCES academic_years(academic_year_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS sections (
    section_id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    program TEXT NOT NULL,
    year_level INTEGER CHECK(year_level BETWEEN 1 AND 6)
);
CREATE TABLE IF NOT EXISTS attendance_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id TEXT NOT NULL,
    section_id INTEGER NOT NULL,
    term_id INTEGER NOT NULL,
    teacher_id INTEGER,
    session_date TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(subject_id, section_id, term_id, session_date),
    FOREIGN KEY(subject_id) REFERENCES subjects(subject_id) ON DELETE CASCADE,
    FOREIGN KEY(section_id) REFERENCES sections(section_id) ON DELETE CASCADE,
    FOREIGN KEY(term_id) REFERENCES terms(term_id) ON DELETE CASCADE,
    FOREIGN KEY(teacher_id) REFERENCES teachers(teacher_id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS attendance_records (
    session_id INTEGER NOT NULL,
    enrollment_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('Present','Absent','Late','Excused')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(session_id, enrollment_id),
    FOREIGN KEY(session_id) REFERENCES attendance_sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY(enrollment_id) REFERENCES enrollments(enrollment_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, action TEXT NOT NULL, detail TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS password_resets (
    reset_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_att_sessions_date ON attendance_sessions(subject_id, section_id, session_date);
"""

# (table, column definition) pairs added with ALTER TABLE when missing.
V2_COLUMNS = [
    ("students", "section_id INTEGER REFERENCES sections(section_id) ON DELETE SET NULL"),
    ("students", "year_level INTEGER"),
    ("students", "contact TEXT"),
    ("students", "status TEXT NOT NULL DEFAULT 'Active'"),
    ("students", "avatar TEXT"),
    ("teachers", "contact TEXT"),
    ("teachers", "avatar TEXT"),
    ("enrollments", "section_id INTEGER REFERENCES sections(section_id) ON DELETE SET NULL"),
    ("enrollments", "term_id INTEGER REFERENCES terms(term_id) ON DELETE SET NULL"),
    ("users", "contact TEXT"),
    ("users", "avatar TEXT"),
]


def _columns(db, table):
    return {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}


def _rebuild_users_for_admin(db):
    """SQLite cannot alter a CHECK constraint, so copy users into a new table.
    Rows and ids are preserved; this is the documented SQLite rebuild procedure."""
    sql = db.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()["sql"]
    if "'Admin'" in sql:
        return
    db.executescript("""
    PRAGMA foreign_keys=OFF;
    BEGIN;
    CREATE TABLE users_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        login_id TEXT NOT NULL UNIQUE,
        email TEXT UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('Student','Teacher','Admin')),
        active INTEGER NOT NULL DEFAULT 1
    );
    INSERT INTO users_new(id,login_id,email,password_hash,role,active)
        SELECT id,login_id,email,password_hash,role,active FROM users;
    DROP TABLE users;
    ALTER TABLE users_new RENAME TO users;
    COMMIT;
    PRAGMA foreign_keys=ON;
    """)


def _backfill_legacy(db):
    """Carry existing data into the v2 structures without inventing anything."""
    # Terms from whatever year/semester pairs already exist on enrollments.
    for year, sem in db.execute("SELECT DISTINCT academic_year, semester FROM enrollments").fetchall():
        db.execute("INSERT OR IGNORE INTO academic_years(label) VALUES(?)", (year,))
        yid = db.execute("SELECT academic_year_id FROM academic_years WHERE label=?", (year,)).fetchone()[0]
        db.execute("INSERT OR IGNORE INTO terms(academic_year_id,semester) VALUES(?,?)", (yid, sem))
    if not db.execute("SELECT 1 FROM terms WHERE is_current=1").fetchone():
        db.execute("UPDATE terms SET is_current=1 WHERE term_id=("
                   "SELECT term_id FROM terms t JOIN academic_years y USING(academic_year_id) "
                   "ORDER BY y.label DESC, t.semester ASC LIMIT 1)")
        db.execute("UPDATE academic_years SET is_current=1 WHERE academic_year_id=("
                   "SELECT academic_year_id FROM terms WHERE is_current=1)")
    db.execute("UPDATE enrollments SET term_id=(SELECT t.term_id FROM terms t "
               "JOIN academic_years y ON y.academic_year_id=t.academic_year_id "
               "WHERE y.label=enrollments.academic_year AND t.semester=enrollments.semester) "
               "WHERE term_id IS NULL")
    # Placeholder sections (one per legacy course) for an admin to correct later.
    for (course,) in db.execute("SELECT DISTINCT course FROM students WHERE section_id IS NULL").fetchall():
        code = f"{course} - Unassigned"
        db.execute("INSERT OR IGNORE INTO sections(code,program) VALUES(?,?)", (code, course))
        sid = db.execute("SELECT section_id FROM sections WHERE code=?", (code,)).fetchone()[0]
        db.execute("UPDATE students SET section_id=? WHERE course=? AND section_id IS NULL", (sid, course))
    db.execute("UPDATE enrollments SET section_id=(SELECT section_id FROM students s "
               "WHERE s.student_id=enrollments.student_id) WHERE section_id IS NULL")


def _repair_wrong_assessment_shape(db):
    """My first v2 draft created assessments with a 'title' column. Your app uses 'name'.
    Only that draft ever had 'title', so those tables are dropped (data was derived from
    the untouched legacy grades table). Your own assessments tables are never touched."""
    cols = _columns(db, "assessments")
    if "title" in cols:
        db.executescript("DROP TABLE IF EXISTS assessment_scores; DROP TABLE assessments;"
                         "DROP TABLE IF EXISTS recitation_scores;")


def migrate(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    _rebuild_users_for_admin(db)
    _repair_wrong_assessment_shape(db)
    db.executescript(BASE_SCHEMA)
    db.executescript(V2_SCHEMA)
    for table, col_def in V2_COLUMNS:
        if col_def.split()[0] not in _columns(db, table):
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_enroll_subject_section "
               "ON enrollments(subject_id, section_id, term_id)")
    if version < 2:  # one-time legacy carry-over; never re-run on already-migrated databases
        _backfill_legacy(db)
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    db.commit()


def init_db():
    with get_db() as db:
        db.executescript(BASE_SCHEMA)
        migrate(db)


def link_legacy_records(db):
    """Idempotent: make sure a current term exists and enrollments point at terms/sections.

    Runs after seeding, so databases created fresh (not migrated) also get terms. Placeholder
    sections are only made for students whose enrollments have no section, so students created
    later by an admin are never given placeholders.
    """
    if not db.execute("SELECT 1 FROM terms").fetchone():
        db.execute("INSERT OR IGNORE INTO academic_years(label,is_current) VALUES('2026-2027',1)")
        yid = db.execute("SELECT academic_year_id FROM academic_years WHERE label='2026-2027'").fetchone()[0]
        db.execute("INSERT INTO terms(academic_year_id,semester,is_current) VALUES(?,'First Semester',1)", (yid,))
        db.execute("INSERT INTO terms(academic_year_id,semester,is_current) VALUES(?,'Second Semester',0)", (yid,))
    db.execute("UPDATE enrollments SET term_id=(SELECT t.term_id FROM terms t "
               "JOIN academic_years y ON y.academic_year_id=t.academic_year_id "
               "WHERE y.label=enrollments.academic_year AND t.semester=enrollments.semester) "
               "WHERE term_id IS NULL")
    for (course,) in db.execute(
            "SELECT DISTINCT course FROM students WHERE section_id IS NULL AND student_id IN "
            "(SELECT student_id FROM enrollments WHERE section_id IS NULL)").fetchall():
        code = f"{course} - Unassigned"
        db.execute("INSERT OR IGNORE INTO sections(code,program) VALUES(?,?)", (code, course))
        sid = db.execute("SELECT section_id FROM sections WHERE code=?", (code,)).fetchone()[0]
        db.execute("UPDATE students SET section_id=? WHERE course=? AND section_id IS NULL "
                   "AND student_id IN (SELECT student_id FROM enrollments WHERE section_id IS NULL)", (sid, course))
    db.execute("UPDATE enrollments SET section_id=(SELECT section_id FROM students s "
               "WHERE s.student_id=enrollments.student_id) WHERE section_id IS NULL")
