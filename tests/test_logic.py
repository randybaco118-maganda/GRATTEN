from backend.logic import calculate_total_grade, calculate_attendance

def test_weighted_grade_rounding():
    assert calculate_total_grade({"output":95,"quiz":92,"recitation":88,"exam":90}) == round(95*.25+92*.20+88*.15+90*.40)

def test_three_lates_make_one_absence():
    result = calculate_attendance({"present":10,"late":3,"absent":8})
    assert result["converted_absences"] == 1
    assert result["total_absences"] == 9
    assert result["is_passed"] is False

def test_eight_absences_passes():
    result = calculate_attendance({"present":10,"late":2,"absent":8})
    assert result["total_absences"] == 8
    assert result["is_passed"] is True
