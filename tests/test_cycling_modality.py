from garmin.cycling_modality import classify_cycling_modality


def test_classifies_modalities_from_explicit_garmin_fields():
    assert classify_cycling_modality({"sport": "cycling", "sport_profile_name": "Bici", "sub_sport": "generic"}) == "Ruta"
    assert classify_cycling_modality({"sport": "cycling", "sport_profile_name": "MTB", "sub_sport": "generic"}) == "MTB"
    assert classify_cycling_modality({"sport": "cycling", "sub_sport": "virtual_activity"}) == "Indoor"
    assert classify_cycling_modality({"sport": "cycling", "sport_profile_name": "Gravel"}) == "Gravel"


def test_unknown_cycling_is_not_guessed_and_non_cycling_is_none():
    assert classify_cycling_modality({"sport": "cycling", "sub_sport": "generic"}) == "Desconocida"
    assert classify_cycling_modality({"sport": "running", "sport_profile_name": "Trail running"}) is None
