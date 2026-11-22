"""Profile, avatar, password change and password recovery API.

Registered from app.py with register(app, login_required).
"""
import hashlib
import os
import re
import secrets
import smtplib
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

from flask import jsonify, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

from .database import ROOT, get_db

MAX_AVATAR_BYTES = 2 * 1024 * 1024
RESET_MINUTES = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
AVATAR_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(png|jpg|webp)$")
MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
GENERIC_FORGOT = "If that account exists and has an email address, a reset link has been sent."


def avatar_dir():
    return Path(os.environ.get("GRATTEN_UPLOADS") or ROOT / "instance" / "avatars")


def sniff_image(data):
    """Return png/jpg/webp from the file's real header bytes, never from its filename."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def deliver_reset_link(email, link):
    """Send the reset email via SMTP if SMTP_HOST is set; otherwise print it (development)."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        print(f"[GRATTEN] Password reset link for {email}: {link}", flush=True)
        return
    msg = EmailMessage()
    msg["Subject"] = "GRATTEN password reset"
    msg["From"] = os.environ.get("SMTP_FROM", "no-reply@gratten.local")
    msg["To"] = email
    msg.set_content(f"Use this link to reset your GRATTEN password (valid for {RESET_MINUTES} minutes):\n\n{link}\n\n"
                    "If you did not request this, you can ignore this email.")
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=10) as smtp:
        smtp.starttls()
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def register(app, login_required):
    app.config.setdefault("MAX_CONTENT_LENGTH", 3 * 1024 * 1024)

    def err(msg, code=400):
        return jsonify(error=msg), code

    def body():
        p = request.get_json(silent=True)
        return p if isinstance(p, dict) else {}

    def audit(db, uid, action, detail=""):
        db.execute("INSERT INTO audit_log(user_id,action,detail) VALUES(?,?,?)", (uid, action, detail))

    def remove_avatar_file(name):
        if name and AVATAR_NAME_RE.match(name):
            (avatar_dir() / name).unlink(missing_ok=True)

    def load_profile(db, uid):
        r = db.execute(
            "SELECT u.id,u.login_id,u.email,u.role,u.contact,u.avatar,"
            "s.student_id,s.name AS s_name,s.course,s.year_level,s.status,sec.code AS section_code,"
            "t.name AS t_name FROM users u "
            "LEFT JOIN students s ON s.user_id=u.id LEFT JOIN sections sec ON sec.section_id=s.section_id "
            "LEFT JOIN teachers t ON t.user_id=u.id WHERE u.id=?", (uid,)).fetchone()
        p = {"login_id": r["login_id"], "email": r["email"], "role": r["role"], "contact": r["contact"],
             "name": r["s_name"] or r["t_name"] or r["login_id"],
             "avatar_url": f"/api/avatars/{r['avatar']}" if r["avatar"] else None}
        if r["student_id"]:
            p.update(student_id=r["student_id"], course=r["course"], year_level=r["year_level"],
                     section=r["section_code"], status=r["status"])
        return p

    @app.get("/api/profile")
    @login_required
    def pr_get(user):
        with get_db() as db:
            return jsonify(profile=load_profile(db, user["id"]))

    @app.put("/api/profile")
    @login_required
    def pr_update(user):
        p = body()
        sets, vals = [], []
        if "email" in p:
            email = p["email"].strip() if isinstance(p["email"], str) else ""
            if not EMAIL_RE.match(email) or len(email) > 120:
                return err("Enter a valid email address.")
            sets.append("email=?"); vals.append(email)
        if "contact" in p:
            contact = p["contact"].strip() if isinstance(p["contact"], str) else ""
            if len(contact) > 40:
                return err("Contact information is too long (40 characters max).")
            sets.append("contact=?"); vals.append(contact or None)
        name = None
        if "name" in p:
            if user["role"] != "Teacher":
                return err("Only teachers can edit their name here. Ask an administrator to correct school records.", 403)
            name = p["name"].strip() if isinstance(p["name"], str) else ""
            if not name or len(name) > 120:
                return err("Enter your full name.")
        if not sets and name is None:
            return err("Nothing to update.")
        try:
            with get_db() as db:
                if sets:
                    db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*vals, user["id"]))
                if name is not None:
                    db.execute("UPDATE teachers SET name=? WHERE user_id=?", (name, user["id"]))
                if "email" in p:
                    audit(db, user["id"], "change_email")
                result = load_profile(db, user["id"])
        except sqlite3.IntegrityError:
            return err("That email address is already in use.", 409)
        return jsonify(profile=result)

    @app.post("/api/profile/avatar")
    @login_required
    def pr_avatar_upload(user):
        f = request.files.get("avatar")
        if not f:
            return err("Choose an image to upload.")
        data = f.read(MAX_AVATAR_BYTES + 1)
        if len(data) > MAX_AVATAR_BYTES:
            return err("Image is too large (2 MB max).")
        ext = sniff_image(data)
        if not ext:
            return err("Only PNG, JPEG or WebP images are allowed.")
        name = f"{uuid.uuid4().hex}.{ext}"
        avatar_dir().mkdir(parents=True, exist_ok=True)
        (avatar_dir() / name).write_bytes(data)
        with get_db() as db:
            old = db.execute("SELECT avatar FROM users WHERE id=?", (user["id"],)).fetchone()["avatar"]
            db.execute("UPDATE users SET avatar=? WHERE id=?", (name, user["id"]))
            result = load_profile(db, user["id"])
        remove_avatar_file(old)
        return jsonify(profile=result)

    @app.delete("/api/profile/avatar")
    @login_required
    def pr_avatar_delete(user):
        with get_db() as db:
            old = db.execute("SELECT avatar FROM users WHERE id=?", (user["id"],)).fetchone()["avatar"]
            db.execute("UPDATE users SET avatar=NULL WHERE id=?", (user["id"],))
            result = load_profile(db, user["id"])
        remove_avatar_file(old)
        return jsonify(profile=result)

    @app.get("/api/avatars/<filename>")
    @login_required
    def pr_avatar_file(user, filename):
        if not AVATAR_NAME_RE.match(filename):
            return err("Not found.", 404)
        resp = send_from_directory(avatar_dir(), filename, mimetype=MIME[filename.rsplit(".", 1)[1]])
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "private, max-age=300"
        return resp

    @app.post("/api/account/password")
    @login_required
    def pr_change_password(user):
        p = body()
        current = p.get("current_password") if isinstance(p.get("current_password"), str) else ""
        new = p.get("new_password") if isinstance(p.get("new_password"), str) else ""
        with get_db() as db:
            row = db.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
            if not check_password_hash(row["password_hash"], current):
                return err("Your current password is incorrect.")
            if len(new) < 8:
                return err("New password must be at least 8 characters.")
            if new == current:
                return err("New password must be different from the current one.")
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), user["id"]))
            audit(db, user["id"], "change_password")
        return jsonify(message="Password changed.")

    # ---- Password recovery (public) ----------------------------------------
    @app.post("/api/auth/forgot")
    def pr_forgot():
        identity = body().get("identity")
        identity = identity.strip() if isinstance(identity, str) else ""
        link = None
        email = None
        if identity:
            with get_db() as db:
                u = db.execute("SELECT id,email FROM users WHERE (login_id=? OR email=?) AND active=1",
                               (identity, identity)).fetchone()
                if u and u["email"]:
                    token = secrets.token_urlsafe(32)
                    now = _now()
                    db.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL",
                               (now.isoformat(), u["id"]))
                    db.execute("INSERT INTO password_resets(user_id,token_hash,expires_at) VALUES(?,?,?)",
                               (u["id"], _hash(token), (now + timedelta(minutes=RESET_MINUTES)).isoformat()))
                    audit(db, u["id"], "request_password_reset")
                    base = os.environ.get("GRATTEN_BASE_URL", request.host_url).rstrip("/")
                    link, email = f"{base}/?reset={token}", u["email"]
        if link:
            try:
                deliver_reset_link(email, link)
            except Exception as exc:  # never reveal delivery problems to the requester
                print(f"[GRATTEN] Could not send reset email: {exc}", flush=True)
        return jsonify(message=GENERIC_FORGOT)

    @app.post("/api/auth/reset")
    def pr_reset():
        p = body()
        token = p.get("token") if isinstance(p.get("token"), str) else ""
        new = p.get("new_password") if isinstance(p.get("new_password"), str) else ""
        if len(new) < 8:
            return err("New password must be at least 8 characters.")
        invalid = err("This reset link is invalid or has expired.")
        with get_db() as db:
            row = db.execute("SELECT reset_id,user_id,expires_at,used_at FROM password_resets WHERE token_hash=?",
                             (_hash(token),)).fetchone()
            if not row or row["used_at"] or datetime.fromisoformat(row["expires_at"]) < _now():
                return invalid
            db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new), row["user_id"]))
            db.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL",
                       (_now().isoformat(), row["user_id"]))
            audit(db, row["user_id"], "reset_password")
        return jsonify(message="Password updated. You can now log in.")

    @app.errorhandler(413)
    def pr_too_large(_):
        return err("Upload is too large.", 413)
