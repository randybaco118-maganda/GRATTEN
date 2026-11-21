from werkzeug.security import generate_password_hash
from .database import get_db, init_db

SUBJECTS = [
    ("DB101","FDMS","FUNDAMENTAL OF DATABASE",3,"Mrs. Ruby Cruz"),
    ("WD102","WEBD","WEB DEVELOPMENT & DESIGN",3,"Mr. Marco Reyes"),
    ("OOP103","OOP","OBJECT ORIENTED PROGRAMMING",3,"Mrs. Ruby Cruz"),
    ("DSA104","DSA","DATA STRUCTURES & ALGORITHMS",3,"Prof. Elena Santos"),
    ("DM105","DISC","DISCRETE MATHEMATICS",3,"Prof. David Lim"),
    ("OS106","OPSYS","OPERATING SYSTEMS",3,"Mr. Marco Reyes"),
    ("NC107","NETCOM","NETWORKING & COMMUNICATIONS",3,"Prof. Elena Santos"),
]
STUDENTS = [
    ("2026-8812","Alex Rivera","BSIT","rbaco25a-0030@olopsc.edu.ph"),
    ("2026-8813","Bianca Santos","BSCS","bsantos26-8813@olopsc.edu.ph"),
    ("2026-8814","Christian Cruz","BSIT","ccruz26-8814@olopsc.edu.ph"),
    ("2026-8815","Diane Mendoza","BSIS","dmendoza26-8815@olopsc.edu.ph"),
    ("2026-8816","Ethan Reyes","BSCS","ereyes26-8816@olopsc.edu.ph"),
    ("2026-8817","Fiona Garcia","BSIT","fgarcia26-8817@olopsc.edu.ph"),
    ("2026-8818","Gabriel Torralba","BSIS","gtorralba26-8818@olopsc.edu.ph"),
    ("2026-8819","Hannah Lee","BSCS","hlee26-8819@olopsc.edu.ph"),
    ("2026-8820","Ian Dela Cruz","BSIT","idelacruz26-8820@olopsc.edu.ph"),
    ("2026-8821","Jasmine Valdez","BSIS","jvaldez26-8821@olopsc.edu.ph"),
    ("2026-8822","Kevin Ramos","BSCS","kramos26-8822@olopsc.edu.ph"),
]
BASE = {
 "2026-8812":(92,88,95,90,22,1,0), "2026-8813":(85,80,82,84,20,2,1),
 "2026-8814":(74,70,72,68,14,3,8), "2026-8815":(95,96,98,94,23,0,0),
 "2026-8816":(80,85,88,82,19,3,1), "2026-8817":(65,68,70,60,15,4,6),
 "2026-8818":(88,90,92,89,21,1,1), "2026-8819":(98,95,96,97,23,0,0),
 "2026-8820":(78,75,80,76,18,2,3), "2026-8821":(90,89,91,92,22,1,0),
 "2026-8822":(72,68,74,70,16,1,6),
}

def seed_demo_data():
    init_db()
    with get_db() as db:
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
        # Demo-only credentials. Replace these accounts/passwords before real deployment.
        for sid, name, course, email in STUDENTS:
            cur = db.execute(
                "INSERT INTO users(login_id,email,password_hash,role) VALUES(?,?,?,?)",
                (sid, email, generate_password_hash("Student123!"), "Student")
            )
            db.execute("INSERT INTO students(student_id,user_id,name,course) VALUES(?,?,?,?)",
                       (sid, cur.lastrowid, name, course))
        teacher = db.execute(
            "INSERT INTO users(login_id,email,password_hash,role) VALUES(?,?,?,?)",
            ("teacher@olopsc.edu.ph","teacher@olopsc.edu.ph",
             generate_password_hash("Teacher123!"),"Teacher")
        )
        db.execute("INSERT INTO teachers(user_id,name) VALUES(?,?)",
                   (teacher.lastrowid,"Mrs. Ruby Cruz"))
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
                    rec = clamp(b[1] - offset)
                    output = clamp(b[2] + (offset % 3))
                    exam = clamp(b[3] + offset)
                    present = max(12, min(23, b[4] - (sidx % 3)))
                    late = (b[5] + sidx) % 4
                    absent = max(0, b[6] - (1 if stidx % 2 == 0 else 0))
                cur = db.execute(
                    "INSERT INTO enrollments(student_id,subject_id) VALUES(?,?)",
                    (sid, subject_id)
                )
                eid = cur.lastrowid
                db.execute("INSERT INTO grades(enrollment_id,quiz,recitation,output,exam) VALUES(?,?,?,?,?)",
                           (eid, quiz, rec, output, exam))
                db.execute("INSERT INTO attendance(enrollment_id,present,late,absent) VALUES(?,?,?,?)",
                           (eid, present, late, absent))
