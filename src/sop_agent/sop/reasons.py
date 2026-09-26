"""Why each SOP step matters (SPEC §9.3.3).

Directives reference these by key; the responder may reword a reason but must not change its meaning.
"""

from enum import StrEnum


class ReasonKey(StrEnum):
    IDENTITY_VERIFICATION = "identity_verification"
    ID_LAST4 = "id_last4"
    ATTEMPT_LIMIT = "attempt_limit"
    REPRESENTATIVE_AUTHORIZATION = "representative_authorization"
    POLICYHOLDER_CONSENT = "policyholder_consent"
    EMAIL_CONSENT = "email_consent"
    DEADLINE_PASSED = "deadline_passed"
    OUT_OF_SCOPE = "out_of_scope"
    PROFESSIONAL_ADVICE = "professional_advice"


REASONS: dict[ReasonKey, str] = {
    ReasonKey.IDENTITY_VERIFICATION: (
        "Claim details include health and financial information, so identity must be confirmed first to keep "
        "anyone else from seeing the records."
    ),
    ReasonKey.ID_LAST4: (
        "Only the last four digits are needed; if the caller would rather not share them, the phone or email on "
        "file works instead."
    ),
    ReasonKey.ATTEMPT_LIMIT: (
        "To protect the account, a live agent needs to help after several unsuccessful attempts."
    ),
    ReasonKey.REPRESENTATIVE_AUTHORIZATION: (
        "An account can be discussed only with the policyholder or someone they've authorized."
    ),
    ReasonKey.POLICYHOLDER_CONSENT: (
        "The policyholder approves in real time so they stay in control of who sees their health information."
    ),
    ReasonKey.EMAIL_CONSENT: (
        "The summary is sent only if the caller agrees; some people prefer not to receive account details by email."
    ),
    ReasonKey.DEADLINE_PASSED: (
        "The deadline has passed, so a claims specialist needs to review the options for this specific situation."
    ),
    ReasonKey.OUT_OF_SCOPE: "This channel handles only insurance claim and policy questions.",
    ReasonKey.PROFESSIONAL_ADVICE: "Medical, legal, and financial questions need the right professional.",
}


def reason(key: ReasonKey) -> str:
    return REASONS[key]
