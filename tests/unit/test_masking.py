from sop_agent.observability.masking import (
    MASKED_DOB,
    MASKED_ID,
    REDACTED_KEY,
    mask_data,
    mask_email,
    mask_phone,
    mask_text,
)

DOT = "\N{BULLET}"


def test_email_keeps_first_letter_and_domain() -> None:
    assert mask_email("margaret@email.com") == f"m{DOT * 7}@email.com"


def test_phone_keeps_last_two_digits() -> None:
    assert mask_phone("+16505212836") == f"{DOT * 3}-{DOT * 3}-{DOT * 2}36"


def test_free_text_masks_dob_id_phone_email_and_api_keys() -> None:
    text = "DOB 1985-03-15, SSN 4472, call 650-521-2836 or margaret@email.com, key sk-ant-abc123_XYZ"
    masked = mask_text(text)
    for secret in ("1985-03-15", "4472", "650-521-2836", "margaret@email.com", "sk-ant-abc123_XYZ"):
        assert secret not in masked
    assert MASKED_DOB in masked and MASKED_ID in masked and REDACTED_KEY in masked
    assert "@email.com" in masked


def test_structures_are_masked_by_key_and_content() -> None:
    data = {
        "dob": "1985-03-15",
        "id_last4": "4472",
        "phone": "6505212836",
        "email": "margaret@email.com",
        "anthropic_api_key": "sk-ant-secret",
        "nested": [{"text": "my SSN last four is 4472"}],
        "count": 3,
    }
    masked = mask_data(data)
    assert masked["dob"] == MASKED_DOB and masked["id_last4"] == MASKED_ID
    assert masked["phone"].endswith("36") and masked["email"].startswith("m")
    assert masked["anthropic_api_key"] == REDACTED_KEY
    assert "4472" not in str(masked["nested"]) and masked["count"] == 3


def test_case_ids_stay_readable() -> None:
    assert "CL-2048" in mask_text("claim CL-2048")
