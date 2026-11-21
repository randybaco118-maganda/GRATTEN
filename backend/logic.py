"""Authoritative GRATTEN grading and attendance rules."""

def calculate_total_grade(record):
    # Output 25%, Quizzes 20%, Recitation 15%, Exam 40%; rounded to integer.
    weighted = (
        float(record["output"]) * 0.25
        + float(record["quiz"]) * 0.20
        + float(record["recitation"]) * 0.15
        + float(record["exam"]) * 0.40
    )
    return round(weighted)

def calculate_attendance(record):
    converted_absences = int(record["late"]) // 3
    total_absences = int(record["absent"]) + converted_absences
    passed = total_absences <= 8
    return {
        "present": int(record["present"]),
        "late": int(record["late"]),
        "raw_absent": int(record["absent"]),
        "converted_absences": converted_absences,
        "total_absences": total_absences,
        "is_passed": passed,
        "status_label": "Attendance Passed" if passed else "FA / Fail",
    }
