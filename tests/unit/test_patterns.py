from datetime import date

import pytest

from sop_agent.domain.enums import IdentityField
from sop_agent.nlu.patterns import extract_patterns

TODAY = date(2026, 3, 10)
F = IdentityField


def test_brief_sentence_yields_policy_dob_and_id_last4() -> None:
    text = (
        "I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare "
        "claim from January. DOB is 1985-03-15, SSN last four is 4472."
    )
    result = extract_patterns(text, TODAY)
    assert result.policy_number == "POL-9921"
    assert result.identity == {F.DOB: "1985-03-15", F.ID_LAST4: "4472"}
    assert result.case_id is None


@pytest.mark.parametrize(
    ("text", "email"),
    [
        ("reach me at Margaret@Email.com please", "margaret@email.com"),
        ("it's yawen.li@example.com.", "yawen.li@example.com"),
    ],
)
def test_email(text: str, email: str) -> None:
    assert extract_patterns(text, TODAY).identity[F.EMAIL] == email


@pytest.mark.parametrize(
    "text",
    [
        "my phone is 650-521-2836",
        "call (650) 521-2836",
        "+1 650 521 2836",
        "number 6505212836 thanks",
        "it's +16505212836",
        "650.521.2836",
    ],
)
def test_phone_formats(text: str) -> None:
    assert extract_patterns(text, TODAY).identity[F.PHONE] == "6505212836"


@pytest.mark.parametrize("text", ["my SSN is 123-45-6789", "zip 94107", "code 12345678901234"])
def test_non_phone_digit_runs_are_ignored(text: str) -> None:
    assert F.PHONE not in extract_patterns(text, TODAY).identity


@pytest.mark.parametrize(
    ("text", "case_id"),
    [("about CL-2048", "CL-2048"), ("claim cl2048 please", "CL-2048"), ("Claim CL-9101.", "CL-9101")],
)
def test_case_id_is_normalized(text: str, case_id: str) -> None:
    assert extract_patterns(text, TODAY).case_id == case_id


def test_policy_number_is_uppercased() -> None:
    assert extract_patterns("my policy is pol-1044", TODAY).policy_number == "POL-1044"


@pytest.mark.parametrize(
    "text",
    [
        "DOB is 1985-03-15",
        "date of birth: March 15, 1985 and my phone is 650-521-2836",
        "I was born on 03/15/1985.",
        "Her birthday is March 15th 1985, thanks",
    ],
)
def test_dob_needs_a_cue_and_valid_date(text: str) -> None:
    assert extract_patterns(text, TODAY).identity[F.DOB] == "1985-03-15"


@pytest.mark.parametrize("text", ["March 15, 1985", "DOB is soon", "born in 1985", "DOB 2027-01-01"])
def test_dob_without_cue_or_valid_date_is_ignored(text: str) -> None:
    assert F.DOB not in extract_patterns(text, TODAY).identity


@pytest.mark.parametrize(
    "text",
    [
        "SSN last four is 4472",
        "the last 4 of my SSN: 4472",
        "last four digits 4472",
        "national ID ends in 6688",
    ],
)
def test_id_last4_needs_a_cue(text: str) -> None:
    assert extract_patterns(text, TODAY).identity[F.ID_LAST4] in {"4472", "6688"}


@pytest.mark.parametrize(
    "text",
    ["I have 4472 reasons", "my SSN is 123-45-6789", "SSN? I'd rather not. My zip is 9410"],
)
def test_four_digits_without_cue_or_inside_longer_numbers_are_ignored(text: str) -> None:
    assert F.ID_LAST4 not in extract_patterns(text, TODAY).identity


def test_empty_text_yields_nothing() -> None:
    result = extract_patterns("", TODAY)
    assert (result.identity, result.policy_number, result.case_id) == ({}, None, None)
