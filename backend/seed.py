import os

from werkzeug.security import generate_password_hash
from .database import get_db, init_db, link_legacy_records

SUBJECTS = [
    ("DB101", "FDMS", "FUNDAMENTAL OF DATABASE", 3, "Mrs. Ruby Cruz"),
    ("WD102", "WEBD", "WEB DEVELOPMENT & DESIGN", 3, "Mr. Marco Reyes"),
    ("OOP103", "OOP", "OBJECT ORIENTED PROGRAMMING", 3, "Mrs. Ruby Cruz"),
    ("DSA104", "DSA", "DATA STRUCTURES & ALGORITHMS", 3, "Prof. Elena Santos"),
    ("DM105", "DISC", "DISCRETE MATHEMATICS", 3, "Prof. David Lim"),
    ("OS106", "OPSYS", "OPERATING SYSTEMS", 3, "Mr. Marco Reyes"),
    ("NC107", "NETCOM", "NETWORKING & COMMUNICATIONS", 3, "Prof. Elena Santos"),
]

STUDENTS = [
    ("2026-8812", "Alex Rivera", "BSIT", "rbaco25a-0030@olopsc.edu.ph"),
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
    """Convert legacy percentage columns into assessments, exactly once.

    A flag in app_meta records that this ran, so assessments a teacher deletes
    are never recreated on restart. Databases that already have assessments are
    just marked as done. Nothing is created when there are no legacy grades.
    """
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
        link_legacy_records(db)
        assign_default_teacher_subjects(db)
        migrate_existing_grades_to_assessments(db)
