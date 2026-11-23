"""Shared helper for enrolling a student in a subject."""


def current_term_id(db):
    row = db.execute("SELECT term_id FROM terms WHERE is_current=1 LIMIT 1").fetchone()
    return row["term_id"] if row else None


def enroll_student(db, student_id, subject_id):
    """Enroll one student (with empty grade/attendance rows). Returns True if newly enrolled."""
    st = db.execute("SELECT section_id FROM students WHERE student_id=?", (student_id,)).fetchone()
    if not st:
        return False
    cur = db.execute(
        "INSERT OR IGNORE INTO enrollments(student_id,subject_id,term_id,section_id) VALUES(?,?,?,?)",
        (student_id, subject_id, current_term_id(db), st["section_id"]),
    )
    if cur.rowcount == 0:
        return False
    eid = cur.lastrowid
    db.execute("INSERT OR IGNORE INTO grades(enrollment_id) VALUES(?)", (eid,))
    db.execute("INSERT OR IGNORE INTO attendance(enrollment_id) VALUES(?)", (eid,))
    return True