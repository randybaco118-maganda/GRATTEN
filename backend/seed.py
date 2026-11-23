import os

from werkzeug.security import generate_password_hash
from .database import get_db, init_db, link_legacy_records
from .enrollment import enroll_student

SUBJECTS = [
    ("DB101", "FDMS", "FUNDAMENTAL OF DATABASE", 3, "Mrs. Ruby Cruz"),
    ("WD102", "WEBD", "WEB DEVELOPMENT & DESIGN", 3, "Mr. Marco Reyes"),
    ("OOP103", "OOP", "OBJECT ORIENTED PROGRAMMING", 3, "Mrs. Ruby Cruz"),
    ("DSA104", "DSA", "DATA STRUCTURES & ALGORITHMS", 3, "Prof. Elena Santos"),
    ("DM105", "DISC", "DISCRETE MATHEMATICS", 3, "Prof. David Lim"),
    ("OS106", "OPSYS", "OPERATING SYSTEMS", 3, "Mr. Marco Reyes"),
    ("NC107", "NETCOM", "NETWORKING & COMMUNICATIONS", 3, "Prof. Elena Santos"),
]

# The rbaco25a account is its own student. Change the name here if it is not "Rbaco".
RBACO = ("2026-0030", "Rbaco", "BSIT", "rbaco25a-0030@olopsc.edu.ph")
ALEX_EMAIL = "arivera26-8812@olopsc.edu.ph"

STUDENTS = [
    ("2026-8812", "Alex Rivera", "BSIT", ALEX_EMAIL),
    ("2026-8813", "Bianca Santos", "BSCS", "bsantos26-8813@olopsc.edu.ph"),
    ("2026-8814", "Christian Cruz", "BSIT", "ccruz26-8814@olopsc.edu.ph"),
    ("2026-8815", "Diane Mendoza", "BSIS", "dmendoza26-8815@olopsc.edu.ph"),
    ("2026-8816", "Ethan Reyes", "BSCS", "ereyes26-8816@olopsc.edu.ph"),
    ("2026-8817", "Fiona Garcia", "BSIT", "fgarcia26-8817@olopsc.edu.ph"),
    ("2026-8818", "Gabriel Torralba", "BSIS", "gtorralba26-8818@olopsc.edu.ph"),
    ("2026-8819", "Hannah Lee", "BSCS", "hlee26-8819@olopsc.edu.ph"),
    ("2026-8820", "Ian Dela Cruz", "BSIT", "idelacruz26-8820@olopsc.edu.ph"),
    ("2026-8821", "Jasmine Valdez", "BSIS", "jvaldez26-8821@olopsc.edu.ph"),
    ("2026-8822", "Kevin Ramos", "BSCS", "kramos26-8822@olopsc.edu.ph"),
    RBACO,
]

BASE = {
    "2026-8812": (92, 88, 95, 90, 22, 1, 0),
    "2026-8813": (85, 80, 82, 84, 20, 2, 1),
    "2026-8814": (74, 70, 72, 68, 14, 3, 8),
    "2026-8815": (95, 96, 98, 94, 23, 0, 0),
    "2026-8816": (80, 85, 88, 82, 19, 3, 1),
    "2026-8817": (65, 68, 70, 60, 15, 4, 6),
    "2026-8818": (88, 90, 92, 89, 21, 1, 1),
    "2026-8819": (98, 95, 96, 97, 23, 0, 0),
    "2026-8820": (78, 75, 80, 76, 18, 2, 3),
    "2026-8821": (90, 89, 91, 92, 22, 1, 0),
    "2026-8822": (72, 68, 74, 70, 16, 1, 6),
    "2026-0030": (88, 85, 90, 87, 21, 1, 1),
}


def assign_default_teacher_subjects(db):
    if db.execute("SELECT COUNT(*) FROM teacher_subjects").fetchone()[0]:
        return
    db.execute(
        "INSERT OR IGNORE INTO teacher_subjects (teacher_id, subject_id) "
        "SELECT t.teacher_id, s.subject_id FROM teachers t "
        "JOIN subjects s ON s.instructor = t.name"
    )


def migrate_existing_grades_to_assessments(db):
    """Convert legacy percentage columns into assessments, exactly once."""
    if db.execute("SELECT 1 FROM app_meta WHERE key='legacy_grades_migrated'").fetchone():
        return
    db.execute("INSERT INTO app_meta(key,value) VALUES('legacy_grades_migrated','1')")
    if db.execute("SELECT COUNT(*) FROM assessments").fetchone()[0]:
        return

    for subj in db.execute("SELECT subject_id FROM subjects").fetchall():
        sid = subj["subject_id"]
        teacher_row = db.execute(
            "SELECT teacher_id FROM teacher_subjects WHERE subject_id=? ORDER BY teacher_id LIMIT 1",
            (sid,),
        ).fetchone()
        tid = teacher_row["teacher_id"] if teacher_row else None

        enrollments = db.execute(
            """
            SELECT e.enrollment_id, g.output, g.quiz, g.exam
            FROM enrollments e
            JOIN grades g ON g.enrollment_id = e.enrollment_id
            WHERE e.subject_id=?
            """,
            (sid,),
        ).fetchall()
        if not enrollments:
            continue

        for title, cat, col_name in [
            ("Legacy Output (migrated)", "Output", "output"),
            ("Legacy Quiz (migrated)", "Quiz", "quiz"),
            ("Legacy Exam (migrated)", "Exam", "exam"),
        ]:
            cur = db.execute(
                "INSERT INTO assessments (subject_id, teacher_id, name, category, max_points) VALUES (?,?,?,?,100)",
                (sid, tid, title, cat),
            )
            for enr in enrollments:
                if enr[col_name] is not None:
                    db.execute(
                        "INSERT OR IGNORE INTO assessment_scores (assessment_id, enrollment_id, earned_points) VALUES (?,?,?)",
                        (cur.lastrowid, enr["enrollment_id"], float(enr[col_name])),
                    )


def ensure_rbaco_account(db):
    """Idempotent. Makes rbaco25a its own student instead of an alias for Alex Rivera.

    Works on existing databases: if Alex still owns the rbaco email, Alex gets a
    different email, then the rbaco student is created and enrolled in every subject.
    Only runs when the demo students exist.
    """
    sid, name, course, email = RBACO
    if not db.execute("SELECT 1 FROM students WHERE student_id='2026-8812'").fetchone():
        return
    owner = db.execute(
        "SELECT u.id, s.student_id FROM users u LEFT JOIN students s ON s.user_id=u.id WHERE u.email=?",
        (email,),
    ).fetchone()
    if owner and owner["student_id"] == "2026-8812":
        db.execute("UPDATE users SET email=? WHERE id=?", (ALEX_EMAIL, owner["id"]))
        owner = None
    if db.execute("SELECT 1 FROM students WHERE student_id=?", (sid,)).fetchone():
        return
    if owner:  # the email belongs to somebody else; do not touch it
        return
    cur = db.execute(
        "INSERT INTO users (login_id,email,password_hash,role) VALUES(?,?,?,'Student')",
        (sid, email, generate_password_hash("Student123!")),
    )
    db.execute(
        "INSERT INTO students (student_id,user_id,name,course) VALUES(?,?,?,?)",
        (sid, cur.lastrowid, name, course),
    )
    for row in db.execute("SELECT subject_id FROM subjects").fetchall():
        enroll_student(db, sid, row["subject_id"])


# Dummy accounts for realistic testing. Password for all students: Student123!
# (student_id, name, course, email, score %, recitation, present, late, absent)
TEST_STUDENTS = [
    ("2026-1001", "Maria Clara Dizon", "BSIT", "mdizon26-1001@olopsc.edu.ph", 92, 95, 22, 0, 0),
    ("2026-1002", "Juan Miguel Pascual", "BSCS", "jpascual26-1002@olopsc.edu.ph", 81, 85, 19, 2, 2),
    ("2026-1003", "Angelica Navarro", "BSIS", "anavarro26-1003@olopsc.edu.ph", 68, 70, 12, 3, 9),  # fails attendance + grade
    ("2026-1004", "Paolo Bautista", "BSIT", "pbautista26-1004@olopsc.edu.ph", 76, 80, 17, 1, 5),
]
TEST_TEACHER = ("marco.reyes@olopsc.edu.ph", "Mr. Marco Reyes")  # password Teacher123!


def ensure_test_accounts(db):
    """Idempotent. Adds a second teacher and four realistic students, enrolled in every
    subject with complete grades and attendance. Development/testing only."""
    if not db.execute("SELECT 1 FROM subjects").fetchone():
        return
    email, tname = TEST_TEACHER
    if not db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        u = db.execute(
            "INSERT INTO users (login_id,email,password_hash,role) VALUES(?,?,?,'Teacher')",
            (email, email, generate_password_hash("Teacher123!")),
        )
        t = db.execute("INSERT INTO teachers (user_id,name) VALUES(?,?)", (u.lastrowid, tname))
        db.execute(
            "INSERT OR IGNORE INTO teacher_subjects (teacher_id, subject_id) "
            "SELECT ?, subject_id FROM subjects WHERE instructor=?",
            (t.lastrowid, tname),
        )

    subjects = [r["subject_id"] for r in db.execute("SELECT subject_id FROM subjects")]
    for sid, name, course, mail, pct, rec, present, late, absent in TEST_STUDENTS:
        if db.execute("SELECT 1 FROM students WHERE student_id=?", (sid,)).fetchone():
            continue
        if db.execute("SELECT 1 FROM users WHERE login_id=? OR email=?", (sid, mail)).fetchone():
            continue
        u = db.execute(
            "INSERT INTO users (login_id,email,password_hash,role) VALUES(?,?,?,'Student')",
            (sid, mail, generate_password_hash("Student123!")),
        )
        db.execute("INSERT INTO students (student_id,user_id,name,course) VALUES(?,?,?,?)",
                   (sid, u.lastrowid, name, course))
        for subject_id in subjects:
            enroll_student(db, sid, subject_id)
            eid = db.execute("SELECT enrollment_id FROM enrollments WHERE student_id=? AND subject_id=?",
                             (sid, subject_id)).fetchone()["enrollment_id"]
            db.execute("UPDATE grades SET recitation=? WHERE enrollment_id=?", (rec, eid))
            db.execute("UPDATE attendance SET present=?, late=?, absent=? WHERE enrollment_id=?",
                       (present, late, absent, eid))
            for a in db.execute("SELECT id, max_points FROM assessments WHERE subject_id=?", (subject_id,)):
                db.execute(
                    "INSERT OR IGNORE INTO assessment_scores (assessment_id, enrollment_id, earned_points) "
                    "VALUES (?,?,?)",
                    (a["id"], eid, round(a["max_points"] * pct / 100, 2)),
                )


def _bootstrap_admin(db):
    """Create the first Admin from GRATTEN_ADMIN_EMAIL / GRATTEN_ADMIN_PASSWORD (if set)."""
    email = os.environ.get("GRATTEN_ADMIN_EMAIL", "").strip()
    password = os.environ.get("GRATTEN_ADMIN_PASSWORD", "")
    if not email or len(password) < 8:
        return
    if db.execute("SELECT 1 FROM users WHERE role='Admin'").fetchone():
        return
    db.execute(
        "INSERT OR IGNORE INTO users (login_id,email,password_hash,role) VALUES(?,?,?,'Admin')",
        (email, email, generate_password_hash(password)),
    )


def _seed_fresh_database(db):
    for sid, name, course, email in STUDENTS:
        cur = db.execute(
            "INSERT INTO users (login_id,email,password_hash,role) VALUES(?,?,?,?)",
            (sid, email, generate_password_hash("Student123!"), "Student"),
        )
        db.execute(
            "INSERT INTO students (student_id,user_id,name,course) VALUES(?,?,?,?)",
            (sid, cur.lastrowid, name, course),
        )

    teacher = db.execute(
        "INSERT INTO users (login_id,email,password_hash,role) VALUES(?,?,?,?)",
        ("teacher@olopsc.edu.ph", "teacher@olopsc.edu.ph", generate_password_hash("Teacher123!"), "Teacher"),
    )
    db.execute(
        "INSERT INTO teachers (user_id,name) VALUES(?,?)",
        (teacher.lastrowid, "Mrs. Ruby Cruz"),
    )

    db.executemany("INSERT INTO subjects VALUES(?,?,?,?,?)", SUBJECTS)

    for sidx, subject in enumerate(SUBJECTS):
        subject_id = subject[0]
        for stidx, (sid, _name, _course, _email) in enumerate(STUDENTS):
            b = BASE[sid]
            if sidx == 0:
                quiz, rec, output, exam, present, late, absent = b
            else:
                offset = ((sidx * 3 + stidx * 2) % 9) - 4
                clamp = lambda v: max(60, min(99, v))
                quiz = clamp(b[0] + offset)
                rec = clamp(b[1] + offset)
                output = clamp(b[2] + (offset % 3))
                exam = clamp(b[3] + offset)
                present = max(12, min(23, b[4] - (sidx % 3)))
                late = (b[5] + sidx) % 4
                absent = max(0, b[6] - (1 if stidx % 2 == 0 else 0))

            cur = db.execute(
                "INSERT INTO enrollments (student_id,subject_id) VALUES(?,?)",
                (sid, subject_id),
            )
            eid = cur.lastrowid
            db.execute(
                "INSERT INTO grades (enrollment_id, quiz, recitation, output, exam) VALUES(?,?,?,?,?)",
                (eid, quiz, rec, output, exam),
            )
            db.execute(
                "INSERT INTO attendance (enrollment_id, present, late, absent) VALUES(?,?,?,?)",
                (eid, present, late, absent),
            )


def seed_demo_data():
    init_db()
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        # Demo data is only inserted into an EMPTY database. Set GRATTEN_SEED_DEMO=0
        # to start completely clean (use GRATTEN_ADMIN_EMAIL/PASSWORD to create the first admin).
        if not db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            if os.environ.get("GRATTEN_SEED_DEMO", "1") == "1":
                _seed_fresh_database(db)
        _bootstrap_admin(db)
        ensure_rbaco_account(db)
        link_legacy_records(db)
        assign_default_teacher_subjects(db)
        migrate_existing_grades_to_assessments(db)
        ensure_test_accounts(db)