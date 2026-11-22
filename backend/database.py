import os
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent.parent


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


def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login_id TEXT NOT NULL UNIQUE,
            email TEXT UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('Student','Teacher')),
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

        -- Which teacher may view and edit which subject.
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

        -- The original prototype stores attendance as counters, not dated events.
        -- This table preserves those exact counters during the first refactor.
        CREATE TABLE IF NOT EXISTS attendance (
            enrollment_id INTEGER PRIMARY KEY,
            present INTEGER NOT NULL DEFAULT 0 CHECK(present >= 0),
            late INTEGER NOT NULL DEFAULT 0 CHECK(late >= 0),
            absent INTEGER NOT NULL DEFAULT 0 CHECK(absent >= 0),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(enrollment_id) REFERENCES enrollments(enrollment_id) ON DELETE CASCADE
        );
        """)
