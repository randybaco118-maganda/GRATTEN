import hashlib
import hmac
import os
import re
import secrets
import smtplib
import sqlite3
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path

from .config import load_env_file, env_flag

load_env_file()

from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash
from .database import get_db, db_path
from .enrollment import enroll_student
from .logic import (
    calculate_attendance,
    calculate_grade_breakdown,
    calculate_total_grade,
    is_valid_number,
    validate_points,
    validate_recitation,
)
from .seed import seed_demo_data

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SECRET = "dev-only-change-this-secret"
MAX_AVATAR_BYTES = 2 * 1024 * 1024

app = Flask(__name__, static_folder="../frontend/static", static_url_path="/static")
app.secret_key = os.environ.get("GRATTEN_SECRET_KEY", DEFAULT_SECRET)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env_flag("COOKIE_SECURE"),
    MAX_CONTENT_LENGTH=4 * 1024 * 1024,
)
if app.config["SESSION_COOKIE_SECURE"] and app.secret_key == DEFAULT_SECRET:
    raise RuntimeError("Set GRATTEN_SECRET_KEY to a long random value when COOKIE_SECURE=1.")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SUBJECT_ID_RE = re.compile(r"^[A-Z0-9-]{2,12}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def new_csrf_token():
    token = secrets.token_urlsafe(32)
    session["csrf_token"] = token
    return token


def ensure_csrf_token():
    return session.get("csrf_token") or new_csrf_token()


@app.before_request
def csrf_protect():
    if request.method in SAFE_METHODS:
        return None
    sent = request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
        return jsonify(error="Invalid or missing CSRF token."), 403
    return None


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    with get_db() as db:
        row = db.execute(
            "SELECT u.id,u.login_id,u.email,u.role,s.student_id,s.name,s.course,"
            "t.teacher_id,t.name AS teacher_name "
            "FROM users u LEFT JOIN students s ON s.user_id=u.id "
            "LEFT JOIN teachers t ON t.user_id=u.id WHERE u.id=? AND u.active=1",
            (uid,),
        ).fetchone()
        if not row:
            session.clear()
            return None
        return dict(row)


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify(error="Authentication required."), 401
        return fn(user, *args, **kwargs)
    return wrapper


def teacher_required(fn):
    @wraps(fn)
    def wrapper(user, *args, **kwargs):
        if user["role"] != "Teacher":
            return jsonify(error="Teacher access required."), 403
        return fn(user, *args, **kwargs)
    return wrapper


def teacher_subject_ids(db, user):
    rows = db.execute(
        "SELECT subject_id FROM teacher_subjects WHERE teacher_id=?",
        (user["teacher_id"],),
    ).fetchall()
    return [r["subject_id"] for r in rows]


def teacher_assigned(db, user, subject_id):
    return (
        db.execute(
            "SELECT 1 FROM teacher_subjects WHERE teacher_id=? AND subject_id=?",
            (user["teacher_id"], subject_id),
        ).fetchone()
        is not None
    )


def not_assigned_response():
    return jsonify(error="You are not assigned to this subject."), 403


def _sync_grades_cache(db, enrollment_id, breakdown):
    comps = breakdown["components"]
    out_pct = comps["output"]["percentage"] if comps["output"]["is_complete"] else 0.0
    quiz_pct = comps["quiz"]["percentage"] if comps["quiz"]["is_complete"] else 0.0
    rec_pct = comps["recitation"]["percentage"] if comps["recitation"]["is_complete"] else 0.0
    exam_pct = comps["exam"]["percentage"] if comps["exam"]["is_complete"] else 0.0
    db.execute(
        """
        UPDATE grades
        SET output=?, quiz=?, recitation=?, exam=?, updated_at=CURRENT_TIMESTAMP
        WHERE enrollment_id=?
        """,
        (out_pct, quiz_pct, rec_pct, exam_pct, enrollment_id),
    )


def _recalculate_subject_grades(db, subject_id):
    enrollments = db.execute(
        """
        SELECT e.enrollment_id, g.recitation
        FROM enrollments e
        LEFT JOIN grades g ON g.enrollment_id = e.enrollment_id
        WHERE e.subject_id=?
        """,
        (subject_id,),
    ).fetchall()
    assessments = db.execute(
        "SELECT id, name, category, max_points, created_at FROM assessments WHERE subject_id=? ORDER BY id",
        (subject_id,),
    ).fetchall()
    scores = db.execute(
        """
        SELECT sc.assessment_id, sc.enrollment_id, sc.earned_points
        FROM assessment_scores sc
        JOIN assessments a ON a.id = sc.assessment_id
        WHERE a.subject_id=?
        """,
        (subject_id,),
    ).fetchall()

    score_map = {(s["assessment_id"], s["enrollment_id"]): s["earned_points"] for s in scores}

    for enr in enrollments:
        eid = enr["enrollment_id"]
        grouped = {"Output": [], "Quiz": [], "Exam": []}
        for a in assessments:
            grouped[a["category"]].append({
                "id": a["id"],
                "name": a["name"],
                "category": a["category"],
                "max_points": a["max_points"],
                "earned_points": score_map.get((a["id"], eid)),
            })
        breakdown = calculate_grade_breakdown(grouped, enr["recitation"])
        _sync_grades_cache(db, eid, breakdown)


def record_for(db, student_id, subject_id):
    row = db.execute(
        """
        SELECT e.enrollment_id, e.student_id, e.subject_id,
               g.quiz, g.recitation, g.output, g.exam,
               a.present, a.late, a.absent,
               s.name AS student_name, s.course, s.section_id,
               sec.code AS section,
               sub.code, sub.name AS subject_name, sub.instructor
        FROM enrollments e
        JOIN grades g ON g.enrollment_id = e.enrollment_id
        JOIN attendance a ON a.enrollment_id = e.enrollment_id
        JOIN students s ON s.student_id = e.student_id
        LEFT JOIN sections sec ON sec.section_id = s.section_id
        JOIN subjects sub ON sub.subject_id = e.subject_id
        WHERE e.student_id=? AND e.subject_id=?
        """,
        (student_id, subject_id),
    ).fetchone()
    if not row:
        return None

    eid = row["enrollment_id"]
    assessments = db.execute(
        "SELECT id, name, category, max_points, created_at FROM assessments WHERE subject_id=? ORDER BY id",
        (subject_id,),
    ).fetchall()
    scores = db.execute(
        "SELECT assessment_id, earned_points FROM assessment_scores WHERE enrollment_id=?",
        (eid,),
    ).fetchall()
    score_by_aid = {sc["assessment_id"]: sc["earned_points"] for sc in scores}

    grouped = {"Output": [], "Quiz": [], "Exam": []}
    for a in assessments:
        grouped[a["category"]].append({
            "id": a["id"],
            "name": a["name"],
            "category": a["category"],
            "max_points": a["max_points"],
            "earned_points": score_by_aid.get(a["id"]),
        })

    breakdown = calculate_grade_breakdown(grouped, row["recitation"])
    comps = breakdown["components"]

    data = dict(row)
    data["breakdown"] = breakdown
    data["is_grade_complete"] = breakdown["is_complete"]
    data["output"] = comps["output"]["percentage"]
    data["quiz"] = comps["quiz"]["percentage"]
    data["recitation"] = comps["recitation"]["percentage"]
    data["exam"] = comps["exam"]["percentage"]
    data["total_grade"] = calculate_total_grade(data)
    data["attendance"] = calculate_attendance(data)
    return data


@app.get("/")
def index():
    return send_from_directory(os.path.join(ROOT, "frontend"), "index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", database="sqlite")


@app.get("/api/csrf")
def csrf():
    return jsonify(csrf_token=ensure_csrf_token())


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
@app.post("/api/auth/login")
def login():
    payload = request.get_json(silent=True) or {}
    identity = str(payload.get("identity", "")).strip()
    password = str(payload.get("password", ""))
    with get_db() as db:
        user = db.execute(
            "SELECT id, login_id, email, password_hash, role FROM users "
            "WHERE (login_id=? OR email=?) AND active=1",
            (identity, identity),
        ).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify(error="Invalid ID/email or password."), 401
        session.clear()
        session["user_id"] = user["id"]
        token = new_csrf_token()
        return jsonify(user=current_user(), csrf_token=token)


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify(message="Logged out.", csrf_token=new_csrf_token())


@app.get("/api/auth/me")
@login_required
def me(user):
    return jsonify(user=user, csrf_token=ensure_csrf_token())


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def send_reset_email(to_address, link):
    """Send via SMTP when GRATTEN_SMTP_HOST is set; otherwise print the link to the server console."""
    host = os.environ.get("GRATTEN_SMTP_HOST", "").strip()
    if not host:
        print(f"[GRATTEN] Password reset link for {to_address}: {link}", flush=True)
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = "GRATTEN password reset"
        msg["From"] = os.environ.get("GRATTEN_SMTP_FROM", "no-reply@gratten.local")
        msg["To"] = to_address
        msg.set_content(
            "Use this link to choose a new password. It expires in 30 minutes.\n\n"
            f"{link}\n\nIf you did not ask for this, ignore this email."
        )
        with smtplib.SMTP(host, int(os.environ.get("GRATTEN_SMTP_PORT", "587")), timeout=15) as smtp:
            if env_flag("GRATTEN_SMTP_TLS", "1"):
                smtp.starttls()
            smtp_user = os.environ.get("GRATTEN_SMTP_USER", "")
            if smtp_user:
                smtp.login(smtp_user, os.environ.get("GRATTEN_SMTP_PASSWORD", ""))
            smtp.send_message(msg)
    except Exception as exc:  # never reveal mail problems to the requester
        app.logger.error("Could not send reset email: %s", exc)


@app.post("/api/auth/forgot")
def forgot_password():
    payload = request.get_json(silent=True) or {}
    identity = str(payload.get("identity", "")).strip()
    generic = "If that account exists and has an email address, a reset link has been sent."
    target = None
    if identity:
        with get_db() as db:
            u = db.execute(
                "SELECT id, email FROM users WHERE (login_id=? OR email=?) AND active=1",
                (identity, identity),
            ).fetchone()
            if u and u["email"]:
                token = secrets.token_urlsafe(32)
                expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
                db.execute("DELETE FROM password_resets WHERE user_id=? AND used_at IS NULL", (u["id"],))
                db.execute(
                    "INSERT INTO password_resets(user_id, token_hash, expires_at) VALUES(?,?,?)",
                    (u["id"], _hash_token(token), expires),
                )
                base = os.environ.get("GRATTEN_BASE_URL", "").strip().rstrip("/") or request.host_url.rstrip("/")
                target = (u["email"], f"{base}/?reset={token}")
    if target:
        send_reset_email(*target)
    return jsonify(message=generic)


@app.post("/api/auth/reset")
def reset_password():
    payload = request.get_json(silent=True) or {}
    token = str(payload.get("token", ""))
    new_password = str(payload.get("new_password", ""))
    if not (8 <= len(new_password) <= 128):
        return jsonify(error="Password must be 8 to 128 characters."), 400
    invalid = jsonify(error="This reset link is invalid or has expired."), 400
    if not token:
        return invalid
    with get_db() as db:
        row = db.execute(
            "SELECT reset_id, user_id, expires_at, used_at FROM password_resets WHERE token_hash=?",
            (_hash_token(token),),
        ).fetchone()
        if not row or row["used_at"]:
            return invalid
        try:
            expired = datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc)
        except ValueError:
            expired = True
        if expired:
            return invalid
        db.execute("UPDATE users SET password_hash=? WHERE id=?",
                   (generate_password_hash(new_password), row["user_id"]))
        db.execute("UPDATE password_resets SET used_at=CURRENT_TIMESTAMP WHERE reset_id=?", (row["reset_id"],))
    return jsonify(message="Password updated. You can now log in.")


@app.post("/api/account/password")
@login_required
def change_password(user):
    payload = request.get_json(silent=True) or {}
    current = str(payload.get("current_password", ""))
    new_password = str(payload.get("new_password", ""))
    if not (8 <= len(new_password) <= 128):
        return jsonify(error="New password must be 8 to 128 characters."), 400
    with get_db() as db:
        row = db.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not row or not check_password_hash(row["password_hash"], current):
            return jsonify(error="Current password is incorrect."), 400
        if check_password_hash(row["password_hash"], new_password):
            return jsonify(error="Choose a password different from the current one."), 400
        db.execute("UPDATE users SET password_hash=? WHERE id=?",
                   (generate_password_hash(new_password), user["id"]))
    return jsonify(message="Password changed.")


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------
def avatar_dir():
    d = db_path().parent / "avatars"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _avatar_url(filename):
    return f"/avatars/{filename}" if filename else None


def _image_ext(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _remove_avatar_file(filename):
    if filename:
        try:
            (avatar_dir() / Path(filename).name).unlink(missing_ok=True)
        except OSError:
            pass


def build_profile(db, user):
    row = db.execute("SELECT login_id,email,role,contact,avatar FROM users WHERE id=?", (user["id"],)).fetchone()
    profile = {
        "role": row["role"],
        "login_id": row["login_id"],
        "email": row["email"],
        "contact": row["contact"],
        "avatar_url": _avatar_url(row["avatar"]),
        "name": user.get("teacher_name") or user.get("name") or row["login_id"],
    }
    if row["role"] == "Student":
        st = db.execute(
            """
            SELECT st.student_id, st.course, st.year_level, st.status,
                   sec.code AS section, sec.year_level AS section_year
            FROM students st LEFT JOIN sections sec ON sec.section_id = st.section_id
            WHERE st.user_id=?
            """,
            (user["id"],),
        ).fetchone()
        if st:
            profile.update(
                student_id=st["student_id"],
                course=st["course"],
                year_level=st["year_level"] or st["section_year"],
                section=st["section"],
                status=st["status"],
            )
    return profile


@app.get("/api/profile")
@login_required
def get_profile(user):
    with get_db() as db:
        return jsonify(profile=build_profile(db, user))


@app.put("/api/profile")
@login_required
def update_profile(user):
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip()
    contact = str(payload.get("contact") or "").strip()
    if not EMAIL_RE.match(email) or len(email) > 120:
        return jsonify(error="Enter a valid email address."), 400
    if len(contact) > 40:
        return jsonify(error="Contact number is too long."), 400

    new_name = None
    if user["role"] == "Teacher" and "name" in payload:
        new_name = " ".join(str(payload["name"]).split())
        if not (2 <= len(new_name) <= 120):
            return jsonify(error="Name must be 2-120 characters."), 400

    with get_db() as db:
        if db.execute("SELECT 1 FROM users WHERE email=? AND id!=?", (email, user["id"])).fetchone():
            return jsonify(error="That email is already used by another account."), 409
        db.execute("UPDATE users SET email=?, contact=? WHERE id=?", (email, contact or None, user["id"]))
        if new_name and new_name != user["teacher_name"]:
            old = user["teacher_name"]
            db.execute("UPDATE teachers SET name=? WHERE teacher_id=?", (new_name, user["teacher_id"]))
            db.execute(
                "UPDATE subjects SET instructor=? WHERE instructor=? AND subject_id IN "
                "(SELECT subject_id FROM teacher_subjects WHERE teacher_id=?)",
                (new_name, old, user["teacher_id"]),
            )
            user = dict(user, teacher_name=new_name)
        return jsonify(profile=build_profile(db, user))


@app.post("/api/profile/avatar")
@login_required
def upload_avatar(user):
    f = request.files.get("avatar")
    if not f:
        return jsonify(error="Choose an image first."), 400
    data = f.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        return jsonify(error="Image is too large (2 MB max)."), 400
    ext = _image_ext(data)
    if not ext:
        return jsonify(error="Use a PNG, JPEG or WebP image."), 400
    filename = f"{user['id']}-{secrets.token_hex(8)}.{ext}"
    (avatar_dir() / filename).write_bytes(data)
    with get_db() as db:
        old = db.execute("SELECT avatar FROM users WHERE id=?", (user["id"],)).fetchone()["avatar"]
        db.execute("UPDATE users SET avatar=? WHERE id=?", (filename, user["id"]))
    _remove_avatar_file(old)
    return jsonify(avatar_url=_avatar_url(filename))


@app.delete("/api/profile/avatar")
@login_required
def delete_avatar(user):
    with get_db() as db:
        old = db.execute("SELECT avatar FROM users WHERE id=?", (user["id"],)).fetchone()["avatar"]
        db.execute("UPDATE users SET avatar=NULL WHERE id=?", (user["id"],))
    _remove_avatar_file(old)
    return jsonify(message="Profile picture removed.")


@app.get("/avatars/<path:filename>")
def serve_avatar(filename):
    resp = send_from_directory(avatar_dir(), filename)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# --------------------------------------------------------------------------
# Subjects
# --------------------------------------------------------------------------
@app.get("/api/subjects")
@login_required
def subjects(user):
    with get_db() as db:
        if user["role"] == "Student":
            rows = db.execute(
                """
                SELECT DISTINCT s.subject_id, s.code, s.name, s.units, s.instructor
                FROM subjects s JOIN enrollments e ON e.subject_id=s.subject_id
                WHERE e.student_id=? ORDER BY s.subject_id
                """,
                (user["student_id"],),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT s.* FROM subjects s
                JOIN teacher_subjects ts ON ts.subject_id=s.subject_id
                WHERE ts.teacher_id=? ORDER BY s.subject_id
                """,
                (user["teacher_id"],),
            ).fetchall()
        return jsonify(subjects=[dict(r) for r in rows])


@app.post("/api/subjects")
@login_required
@teacher_required
def create_subject(user):
    payload = request.get_json(silent=True) or {}
    subject_id = str(payload.get("subject_id", "")).strip().upper()
    code = str(payload.get("code", "")).strip().upper()
    name = " ".join(str(payload.get("name", "")).split())
    units = payload.get("units", 3)

    if not SUBJECT_ID_RE.match(subject_id):
        return jsonify(error="Subject ID must be 2-12 letters, numbers or dashes."), 400
    if not (2 <= len(code) <= 10):
        return jsonify(error="Code must be 2-10 characters."), 400
    if not (3 <= len(name) <= 80):
        return jsonify(error="Name must be 3-80 characters."), 400
    if not (isinstance(units, int) and not isinstance(units, bool) and 1 <= units <= 6):
        return jsonify(error="Units must be a whole number from 1 to 6."), 400

    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO subjects(subject_id,code,name,units,instructor) VALUES(?,?,?,?,?)",
                (subject_id, code, name, units, user["teacher_name"]),
            )
            db.execute(
                "INSERT INTO teacher_subjects(teacher_id,subject_id) VALUES(?,?)",
                (user["teacher_id"], subject_id),
            )
            if payload.get("enroll_all"):
                for s in db.execute("SELECT student_id FROM students").fetchall():
                    enroll_student(db, s["student_id"], subject_id)
    except sqlite3.IntegrityError:
        return jsonify(error="A subject with that ID already exists."), 409

    return jsonify(subject={
        "subject_id": subject_id,
        "code": code,
        "name": name,
        "units": units,
        "instructor": user["teacher_name"],
    }), 201


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------
@app.get("/api/sections")
@login_required
@teacher_required
def list_sections(user):
    with get_db() as db:
        rows = db.execute(
            """
            SELECT sec.section_id, sec.code, sec.program, sec.year_level,
                   COUNT(st.student_id) AS student_count
            FROM sections sec LEFT JOIN students st ON st.section_id = sec.section_id
            GROUP BY sec.section_id ORDER BY sec.code
            """
        ).fetchall()
        return jsonify(sections=[dict(r) for r in rows])


@app.post("/api/sections")
@login_required
@teacher_required
def create_section(user):
    payload = request.get_json(silent=True) or {}
    code = " ".join(str(payload.get("code", "")).split()).upper()
    program = str(payload.get("program", "")).strip().upper()
    year = payload.get("year_level")
    if year in ("", None):
        year = None
    elif not (isinstance(year, int) and not isinstance(year, bool) and 1 <= year <= 6):
        return jsonify(error="Year level must be a whole number from 1 to 6."), 400
    if not (2 <= len(code) <= 30):
        return jsonify(error="Section code must be 2-30 characters."), 400
    if not (2 <= len(program) <= 20):
        return jsonify(error="Program must be 2-20 characters."), 400
    try:
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO sections(code, program, year_level) VALUES(?,?,?)",
                (code, program, year),
            )
            section_id = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error="A section with that code already exists."), 409
    return jsonify(section={"section_id": section_id, "code": code, "program": program,
                            "year_level": year, "student_count": 0}), 201


@app.delete("/api/sections/<int:section_id>")
@login_required
@teacher_required
def delete_section(user, section_id):
    with get_db() as db:
        if not db.execute("SELECT 1 FROM sections WHERE section_id=?", (section_id,)).fetchone():
            return jsonify(error="Section not found."), 404
        if db.execute("SELECT 1 FROM students WHERE section_id=?", (section_id,)).fetchone():
            return jsonify(error="Move or remove the students from this section first."), 409
        db.execute("DELETE FROM sections WHERE section_id=?", (section_id,))
    return jsonify(deleted=True)


@app.get("/api/section-students")
@login_required
@teacher_required
def section_students(user):
    with get_db() as db:
        rows = db.execute(
            """
            SELECT st.student_id, st.name, st.course, st.section_id, sec.code AS section
            FROM students st LEFT JOIN sections sec ON sec.section_id = st.section_id
            ORDER BY st.name
            """
        ).fetchall()
        return jsonify(students=[dict(r) for r in rows])


@app.put("/api/sections/<int:section_id>/students")
@login_required
@teacher_required
def set_section_students(user, section_id):
    payload = request.get_json(silent=True) or {}
    ids = payload.get("student_ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return jsonify(error="student_ids must be a list of student IDs."), 400
    wanted = set(ids)
    with get_db() as db:
        if not db.execute("SELECT 1 FROM sections WHERE section_id=?", (section_id,)).fetchone():
            return jsonify(error="Section not found."), 404
        known = {r["student_id"] for r in db.execute("SELECT student_id FROM students")}
        if not wanted <= known:
            return jsonify(error="Unknown student in the list."), 400
        current = {r["student_id"] for r in db.execute(
            "SELECT student_id FROM students WHERE section_id=?", (section_id,))}
        for sid in current - wanted:  # removed from this section
            db.execute("UPDATE students SET section_id=NULL WHERE student_id=?", (sid,))
            db.execute("UPDATE enrollments SET section_id=NULL WHERE student_id=?", (sid,))
        for sid in wanted:  # added (moves them out of any other section)
            db.execute("UPDATE students SET section_id=? WHERE student_id=?", (section_id, sid))
            db.execute("UPDATE enrollments SET section_id=? WHERE student_id=?", (section_id, sid))
        count = len(wanted)
    return jsonify(section_id=section_id, student_count=count)


@app.post("/api/sections/<int:section_id>/enroll")
@login_required
@teacher_required
def enroll_section(user, section_id):
    payload = request.get_json(silent=True) or {}
    subject_id = str(payload.get("subject_id", "")).strip()
    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()
        if not db.execute("SELECT 1 FROM sections WHERE section_id=?", (section_id,)).fetchone():
            return jsonify(error="Section not found."), 404
        students_in = db.execute(
            "SELECT student_id FROM students WHERE section_id=?", (section_id,)
        ).fetchall()
        if not students_in:
            return jsonify(error="This section has no students yet."), 400
        enrolled = sum(1 for s in students_in if enroll_student(db, s["student_id"], subject_id))
        already = len(students_in) - enrolled
    return jsonify(enrolled=enrolled, already_enrolled=already)


# --------------------------------------------------------------------------
# Students, records, assessments, grades, attendance, results
# --------------------------------------------------------------------------
@app.get("/api/students")
@login_required
def students(user):
    if user["role"] != "Teacher":
        return jsonify(
            students=[{
                "student_id": user["student_id"],
                "name": user["name"],
                "course": user["course"],
            }]
        )
    with get_db() as db:
        rows = db.execute(
            """
            SELECT DISTINCT st.student_id, st.name, st.course
            FROM students st
            JOIN enrollments e ON e.student_id=st.student_id
            JOIN teacher_subjects ts ON ts.subject_id=e.subject_id
            WHERE ts.teacher_id=? ORDER BY st.name
            """,
            (user["teacher_id"],),
        ).fetchall()
        return jsonify(students=[dict(r) for r in rows])


@app.get("/api/records/<subject_id>")
@login_required
def records(user, subject_id):
    with get_db() as db:
        if user["role"] == "Student":
            rows = db.execute(
                "SELECT student_id FROM enrollments WHERE subject_id=? AND student_id=?",
                (subject_id, user["student_id"]),
            ).fetchall()
        else:
            if not teacher_assigned(db, user, subject_id):
                return not_assigned_response()
            rows = db.execute(
                "SELECT student_id FROM enrollments WHERE subject_id=? ORDER BY student_id",
                (subject_id,),
            ).fetchall()

        output = [record_for(db, r["student_id"], subject_id) for r in rows]
        assessments = [
            dict(a)
            for a in db.execute(
                "SELECT id, subject_id, teacher_id, name, category, max_points, created_at "
                "FROM assessments WHERE subject_id=? ORDER BY id",
                (subject_id,),
            ).fetchall()
        ]
        sections = []
        if user["role"] == "Teacher":
            section_rows = db.execute(
                """
                SELECT COALESCE(sec.code, 'Unassigned') AS section_name, COUNT(*) AS student_count
                FROM enrollments e
                JOIN students st ON st.student_id = e.student_id
                LEFT JOIN sections sec ON sec.section_id = st.section_id
                WHERE e.subject_id=?
                GROUP BY section_name
                ORDER BY section_name
                """,
                (subject_id,),
            ).fetchall()
            sections = [dict(s) for s in section_rows]
        return jsonify(records=output, assessments=assessments, sections=sections)


@app.get("/api/assessments/<subject_id>")
@login_required
def list_assessments(user, subject_id):
    with get_db() as db:
        if user["role"] == "Student":
            enrolled = db.execute(
                "SELECT 1 FROM enrollments WHERE subject_id=? AND student_id=?",
                (subject_id, user["student_id"]),
            ).fetchone()
            if not enrolled:
                return jsonify(error="Not enrolled in this subject."), 403
        else:
            if not teacher_assigned(db, user, subject_id):
                return not_assigned_response()
        rows = db.execute(
            "SELECT id, subject_id, teacher_id, name, category, max_points, created_at "
            "FROM assessments WHERE subject_id=? ORDER BY id",
            (subject_id,),
        ).fetchall()
        return jsonify(assessments=[dict(r) for r in rows])


@app.post("/api/assessments/<subject_id>")
@login_required
@teacher_required
def create_assessment(user, subject_id):
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    category = str(payload.get("category", "")).strip()
    max_points = payload.get("max_points")

    if not name:
        return jsonify(error="Assessment name is required."), 400
    if category not in ("Output", "Quiz", "Exam"):
        return jsonify(error="Category must be Output, Quiz, or Exam."), 400
    if not is_valid_number(max_points) or float(max_points) <= 0:
        return jsonify(error="Maximum points must be greater than zero."), 400

    max_pts_val = float(max_points)
    scores_payload = payload.get("scores") or {}

    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()

        enrollments = db.execute(
            "SELECT enrollment_id, student_id FROM enrollments WHERE subject_id=?",
            (subject_id,),
        ).fetchall()
        enr_by_student = {e["student_id"]: e["enrollment_id"] for e in enrollments}

        validated_scores = []
        if isinstance(scores_payload, dict):
            for sid, pts in scores_payload.items():
                if pts is None or str(pts).strip() == "":
                    continue
                if sid not in enr_by_student:
                    return jsonify(error=f"Student {sid} is not enrolled in {subject_id}."), 400
                ok, err = validate_points(pts, max_pts_val)
                if not ok:
                    return jsonify(error=err), 400
                validated_scores.append((enr_by_student[sid], float(pts)))

        cur = db.execute(
            "INSERT INTO assessments (subject_id, teacher_id, name, category, max_points) VALUES (?,?,?,?,?)",
            (subject_id, user["teacher_id"], name, category, max_pts_val),
        )
        assessment_id = cur.lastrowid

        for eid, pts_val in validated_scores:
            db.execute(
                "INSERT INTO assessment_scores (assessment_id, enrollment_id, earned_points) VALUES (?,?,?)",
                (assessment_id, eid, pts_val),
            )

        _recalculate_subject_grades(db, subject_id)
        assessment = dict(
            db.execute(
                "SELECT id, subject_id, teacher_id, name, category, max_points, created_at "
                "FROM assessments WHERE id=?",
                (assessment_id,),
            ).fetchone()
        )
        updated_records = [
            record_for(db, r["student_id"], subject_id)
            for r in db.execute(
                "SELECT student_id FROM enrollments WHERE subject_id=? ORDER BY student_id",
                (subject_id,),
            ).fetchall()
        ]
        return jsonify(assessment=assessment, records=updated_records), 201


@app.put("/api/assessments/<subject_id>/<int:assessment_id>")
@login_required
@teacher_required
def update_assessment(user, subject_id, assessment_id):
    payload = request.get_json(silent=True) or {}
    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()

        existing = db.execute(
            "SELECT id, subject_id, teacher_id, name, category, max_points "
            "FROM assessments WHERE id=? AND subject_id=?",
            (assessment_id, subject_id),
        ).fetchone()
        if not existing:
            return jsonify(error="Assessment not found."), 404

        name = str(payload.get("name", existing["name"])).strip()
        category = str(payload.get("category", existing["category"])).strip()
        max_points = payload.get("max_points", existing["max_points"])

        if not name:
            return jsonify(error="Assessment name is required."), 400
        if category not in ("Output", "Quiz", "Exam"):
            return jsonify(error="Category must be Output, Quiz, or Exam."), 400
        if not is_valid_number(max_points) or float(max_points) <= 0:
            return jsonify(error="Maximum points must be greater than zero."), 400

        max_pts_val = float(max_points)

        existing_scores = db.execute(
            "SELECT id, enrollment_id, earned_points FROM assessment_scores WHERE assessment_id=?",
            (assessment_id,),
        ).fetchall()
        for sc in existing_scores:
            if float(sc["earned_points"]) > max_pts_val:
                return jsonify(
                    error=f"An existing score ({sc['earned_points']}) exceeds the new maximum points ({max_pts_val})."
                ), 400

        scores_payload = payload.get("scores")
        validated_updates = []
        if isinstance(scores_payload, dict):
            enrollments = db.execute(
                "SELECT enrollment_id, student_id FROM enrollments WHERE subject_id=?",
                (subject_id,),
            ).fetchall()
            enr_by_student = {e["student_id"]: e["enrollment_id"] for e in enrollments}
            for sid, pts in scores_payload.items():
                if sid not in enr_by_student:
                    return jsonify(error=f"Student {sid} is not enrolled in {subject_id}."), 400
                if pts is None or str(pts).strip() == "":
                    validated_updates.append((enr_by_student[sid], None))
                else:
                    ok, err = validate_points(pts, max_pts_val)
                    if not ok:
                        return jsonify(error=err), 400
                    validated_updates.append((enr_by_student[sid], float(pts)))

        db.execute(
            "UPDATE assessments SET name=?, category=?, max_points=? WHERE id=? AND subject_id=?",
            (name, category, max_pts_val, assessment_id, subject_id),
        )

        for eid, pts_val in validated_updates:
            if pts_val is None:
                db.execute(
                    "DELETE FROM assessment_scores WHERE assessment_id=? AND enrollment_id=?",
                    (assessment_id, eid),
                )
            else:
                db.execute(
                    """
                    INSERT INTO assessment_scores (assessment_id, enrollment_id, earned_points, updated_at)
                    VALUES (?,?,?,CURRENT_TIMESTAMP)
                    ON CONFLICT(assessment_id, enrollment_id)
                    DO UPDATE SET earned_points=excluded.earned_points, updated_at=CURRENT_TIMESTAMP
                    """,
                    (assessment_id, eid, pts_val),
                )

        _recalculate_subject_grades(db, subject_id)
        updated_assessment = dict(
            db.execute(
                "SELECT id, subject_id, teacher_id, name, category, max_points, created_at "
                "FROM assessments WHERE id=?",
                (assessment_id,),
            ).fetchone()
        )
        updated_records = [
            record_for(db, r["student_id"], subject_id)
            for r in db.execute(
                "SELECT student_id FROM enrollments WHERE subject_id=? ORDER BY student_id",
                (subject_id,),
            ).fetchall()
        ]
        return jsonify(assessment=updated_assessment, records=updated_records)


@app.delete("/api/assessments/<subject_id>/<int:assessment_id>")
@login_required
@teacher_required
def delete_assessment(user, subject_id, assessment_id):
    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()

        existing = db.execute(
            "SELECT id FROM assessments WHERE id=? AND subject_id=?",
            (assessment_id, subject_id),
        ).fetchone()
        if not existing:
            return jsonify(error="Assessment not found."), 404

        db.execute(
            "DELETE FROM assessments WHERE id=? AND subject_id=?",
            (assessment_id, subject_id),
        )
        _recalculate_subject_grades(db, subject_id)
        updated_records = [
            record_for(db, r["student_id"], subject_id)
            for r in db.execute(
                "SELECT student_id FROM enrollments WHERE subject_id=? ORDER BY student_id",
                (subject_id,),
            ).fetchall()
        ]
        return jsonify(deleted=True, assessment_id=assessment_id, records=updated_records)


@app.put("/api/grades/<subject_id>/<student_id>")
@login_required
@teacher_required
def update_grade(user, subject_id, student_id):
    payload = request.get_json(silent=True) or {}

    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()

        enr = db.execute(
            "SELECT enrollment_id FROM enrollments WHERE subject_id=? AND student_id=?",
            (subject_id, student_id),
        ).fetchone()
        if not enr:
            return jsonify(error="Enrollment not found."), 404
        eid = enr["enrollment_id"]

        legacy_keys = ("quiz", "recitation", "output", "exam")
        if all(k in payload for k in legacy_keys) and "assessment_scores" not in payload:
            try:
                values = [float(payload[k]) for k in legacy_keys]
            except (TypeError, ValueError):
                return jsonify(error="Grades must be numeric."), 400
            if any(not is_valid_number(v) or v < 0 or v > 100 for v in values):
                return jsonify(error="Each grade must be between 0 and 100."), 400

            db.execute(
                """
                UPDATE grades
                SET quiz=?, recitation=?, output=?, exam=?, updated_at=CURRENT_TIMESTAMP
                WHERE enrollment_id=?
                """,
                (*values, eid),
            )
            subj_assessments = db.execute(
                "SELECT id, category, max_points FROM assessments WHERE subject_id=?",
                (subject_id,),
            ).fetchall()
            cat_counts = {}
            for a in subj_assessments:
                cat_counts[a["category"]] = cat_counts.get(a["category"], 0) + 1
            col_map = {"Output": values[2], "Quiz": values[0], "Exam": values[3]}
            for a in subj_assessments:
                if cat_counts.get(a["category"]) == 1:
                    scaled = round((col_map[a["category"]] / 100.0) * float(a["max_points"]), 2)
                    db.execute(
                        """
                        INSERT INTO assessment_scores (assessment_id, enrollment_id, earned_points, updated_at)
                        VALUES (?,?,?,CURRENT_TIMESTAMP)
                        ON CONFLICT(assessment_id, enrollment_id)
                        DO UPDATE SET earned_points=excluded.earned_points, updated_at=CURRENT_TIMESTAMP
                        """,
                        (a["id"], eid, scaled),
                    )
            updated = record_for(db, student_id, subject_id)
            return jsonify(record=updated)

        if "recitation" in payload and payload["recitation"] is not None and str(payload["recitation"]).strip() != "":
            ok, err = validate_recitation(payload["recitation"])
            if not ok:
                return jsonify(error=err), 400
            db.execute(
                "UPDATE grades SET recitation=?, updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
                (float(payload["recitation"]), eid),
            )

        if "assessment_scores" in payload:
            items = payload["assessment_scores"]
            if not isinstance(items, list):
                return jsonify(error="assessment_scores must be a list."), 400

            parsed = []
            for entry in items:
                if not isinstance(entry, dict) or "assessment_id" not in entry:
                    return jsonify(error="Each score entry requires assessment_id."), 400
                aid = entry["assessment_id"]
                assess_row = db.execute(
                    "SELECT id, max_points FROM assessments WHERE id=? AND subject_id=?",
                    (aid, subject_id),
                ).fetchone()
                if not assess_row:
                    return jsonify(error=f"Assessment {aid} does not belong to {subject_id}."), 400

                raw_pts = entry.get("earned_points")
                if raw_pts is None or str(raw_pts).strip() == "":
                    parsed.append((assess_row["id"], None))
                else:
                    ok, err = validate_points(raw_pts, assess_row["max_points"])
                    if not ok:
                        return jsonify(error=err), 400
                    parsed.append((assess_row["id"], float(raw_pts)))

            for aid, pts_val in parsed:
                if pts_val is None:
                    db.execute(
                        "DELETE FROM assessment_scores WHERE assessment_id=? AND enrollment_id=?",
                        (aid, eid),
                    )
                else:
                    db.execute(
                        """
                        INSERT INTO assessment_scores (assessment_id, enrollment_id, earned_points, updated_at)
                        VALUES (?,?,?,CURRENT_TIMESTAMP)
                        ON CONFLICT(assessment_id, enrollment_id)
                        DO UPDATE SET earned_points=excluded.earned_points, updated_at=CURRENT_TIMESTAMP
                        """,
                        (aid, eid, pts_val),
                    )

        _recalculate_subject_grades(db, subject_id)
        updated = record_for(db, student_id, subject_id)
        return jsonify(record=updated)


ATTENDANCE_UPDATES = {
    "present": "UPDATE attendance SET present=MAX(0,present+?), updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
    "late": "UPDATE attendance SET late=MAX(0,late+?), updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
    "absent": "UPDATE attendance SET absent=MAX(0,absent+?), updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
}


@app.patch("/api/attendance/<subject_id>/<student_id>")
@login_required
@teacher_required
def update_attendance(user, subject_id, student_id):
    payload = request.get_json(silent=True) or {}
    field = payload.get("field")
    delta = payload.get("delta")
    valid_field = isinstance(field, str) and field in ATTENDANCE_UPDATES
    valid_delta = isinstance(delta, int) and not isinstance(delta, bool) and delta in (-1, 1)
    if not (valid_field and valid_delta):
        return jsonify(error="Use field present/late/absent and delta -1 or 1."), 400
    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()
        row = db.execute(
            "SELECT enrollment_id FROM enrollments WHERE subject_id=? AND student_id=?",
            (subject_id, student_id),
        ).fetchone()
        if not row:
            return jsonify(error="Enrollment not found."), 404
        db.execute(ATTENDANCE_UPDATES[field], (delta, row["enrollment_id"]))
        updated = record_for(db, student_id, subject_id)
        return jsonify(record=updated)


@app.post("/api/attendance/<subject_id>/<student_id>/reset")
@login_required
@teacher_required
def reset_attendance(user, subject_id, student_id):
    with get_db() as db:
        if not teacher_assigned(db, user, subject_id):
            return not_assigned_response()
        row = db.execute(
            "SELECT enrollment_id FROM enrollments WHERE subject_id=? AND student_id=?",
            (subject_id, student_id),
        ).fetchone()
        if not row:
            return jsonify(error="Enrollment not found."), 404
        db.execute(
            "UPDATE attendance SET present=0, late=0, absent=0, updated_at=CURRENT_TIMESTAMP WHERE enrollment_id=?",
            (row["enrollment_id"],),
        )
        updated = record_for(db, student_id, subject_id)
        return jsonify(record=updated)


@app.get("/api/results")
@login_required
def results(user):
    is_teacher = user["role"] == "Teacher"
    with get_db() as db:
        allowed_subjects = teacher_subject_ids(db, user) if is_teacher else None
        student_id = user["student_id"]
        if is_teacher:
            requested = request.args.get("student_id")
            if requested:
                student_id = requested
            else:
                first = db.execute(
                    """
                    SELECT e.student_id FROM enrollments e
                    JOIN teacher_subjects ts ON ts.subject_id=e.subject_id
                    WHERE ts.teacher_id=? ORDER BY e.student_id LIMIT 1
                    """,
                    (user["teacher_id"],),
                ).fetchone()
                student_id = first["student_id"] if first else None

        rows = db.execute(
            "SELECT subject_id FROM enrollments WHERE student_id=? ORDER BY subject_id",
            (student_id,),
        ).fetchall()
        subject_ids = [r["subject_id"] for r in rows]
        if is_teacher:
            subject_ids = [s for s in subject_ids if s in allowed_subjects]

        result_rows = [record_for(db, student_id, sid) for sid in subject_ids]
        result_rows = [r for r in result_rows if r is not None]
        student = db.execute(
            "SELECT student_id, name, course FROM students WHERE student_id=?",
            (student_id,),
        ).fetchone()
        if not student or (is_teacher and not result_rows):
            return jsonify(error="Student not found."), 404

        complete_grades = [r["total_grade"] for r in result_rows if r["total_grade"] is not None]
        average = round(sum(complete_grades) / len(complete_grades), 2) if complete_grades else None
        return jsonify(
            student=dict(student),
            records=result_rows,
            general_average=average,
            is_complete=len(complete_grades) == len(result_rows) and len(result_rows) > 0,
        )


@app.errorhandler(404)
def not_found(_):
    return jsonify(error="Not found."), 404


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="Upload is too large."), 413


with app.app_context():
    seed_demo_data()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=env_flag("FLASK_DEBUG"))