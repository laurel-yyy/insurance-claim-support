"""Sensitive token index, used by the output guard and by evals.

Built once from the repository. Text is scanned by extracting candidate tokens in canonical form (amounts as
decimals, dates as ISO or month-day, phones as 10 digits, IDs upper-cased, names as token sequences) and looking
them up, so every surface form ("$1,450", "1,450.00", "Jan 12") maps to the same key.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.normalize import normalize_email, normalize_name, normalize_phone

NGRAM = 6
_MONTHS = {
    m: i
    for i, names in enumerate(
        (
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
            ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"),
            ("november", "nov"), ("december", "dec"),
        ),
        start=1,
    )
    for m in names
}  # fmt: skip
_MONTH_WORD = "|".join(sorted(_MONTHS, key=len, reverse=True))
_AMOUNT = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?|\b\d{1,3}(?:,\d{3})+(?:\.\d{2})?\b|\b\d+\.\d{2}\b")
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_NAMED = re.compile(
    rf"\b({_MONTH_WORD})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.IGNORECASE
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\d-])")
_ID = re.compile(r"\b(CL|POL)-?(\d+)\b", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9']+")


class TokenKind(StrEnum):
    ID = "id"
    AMOUNT = "amount"
    DATE = "date"
    MONTH_DAY = "month_day"
    EMAIL = "email"
    PHONE = "phone"
    NAME = "name"
    PHRASE = "phrase"


@dataclass(frozen=True)
class Hit:
    kind: TokenKind
    key: str  # Canonical form; never logged raw (it may be record data)
    parties: frozenset[str]


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace("$", "").replace(",", "").strip())
    except InvalidOperation:
        return None


def amounts_in(text: str) -> set[Decimal]:
    found = {_decimal(m.group(0)) for m in _AMOUNT.finditer(text)}
    return {a.quantize(Decimal("0.01")) for a in found if a is not None}


def dates_in(text: str) -> tuple[set[str], set[str]]:
    """(full ISO dates, month-day keys) mentioned in text. Month-only mentions are ignored."""
    full: set[str] = set()
    month_day: set[str] = set()

    def add(year: int | None, month: int, day: int) -> None:
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return
        month_day.add(f"{month:02d}-{day:02d}")
        if year is not None:
            full.add(f"{year:04d}-{month:02d}-{day:02d}")

    for m in _ISO.finditer(text):
        add(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    for m in _NUMERIC.finditer(text):
        add(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    for m in _NAMED.finditer(text):
        add(int(m.group(3)) if m.group(3) else None, _MONTHS[m.group(1).casefold()], int(m.group(2)))
    return full, month_day


def ids_in(text: str) -> set[str]:
    return {f"{m.group(1).upper()}-{m.group(2)}" for m in _ID.finditer(text)}


def emails_in(text: str) -> set[str]:
    return {e for e in (normalize_email(m) for m in _EMAIL.findall(text)) if e}


def phones_in(text: str) -> set[str]:
    return {p for p in (normalize_phone(m) for m in _PHONE.findall(text)) if p}


def words(text: str) -> list[str]:
    return _WORD.findall(text.casefold().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'"))


class SensitiveIndex:
    def __init__(self, repo: InMemoryRepository) -> None:
        self._tokens: dict[tuple[TokenKind, str], set[str]] = defaultdict(set)
        self._names: dict[str, set[str]] = defaultdict(set)
        for holder in repo.policyholders():
            party = holder.party_id
            self._add(TokenKind.ID, _id_key(holder.policy_number), party)
            for email in (holder.email, *holder.email_aliases):
                if (e := normalize_email(email)) is not None:
                    self._add(TokenKind.EMAIL, e, party)
            for phone in (holder.phone, *holder.phone_aliases):
                if (p := normalize_phone(phone)) is not None:
                    self._add(TokenKind.PHONE, p, party)
            for name in (holder.name, *holder.name_aliases):
                if (n := normalize_name(name)) is not None:
                    self._names[n].add(party)
        for rep in repo.representatives():
            if (n := normalize_name(rep.rep_name)) is not None:
                self._names[n].add(rep.buyer_party_id)
        for claim in repo.claims():
            party = claim.party_id
            self._add(TokenKind.ID, _id_key(claim.case_id), party)
            for amount in (
                claim.expected_reimbursement_amount,
                claim.allowed_max_amount,
                claim.net_pay,
                claim.net_fee,
            ):
                if amount:
                    self._add(TokenKind.AMOUNT, str(amount.quantize(Decimal("0.01"))), party)
            for day in (claim.created_at, claim.appeal_deadline):
                if day is not None:
                    self._add_date(day, party)
            for gram in _ngrams(words(claim.denial_reason or "")):
                self._add(TokenKind.PHRASE, gram, party)

    def _add(self, kind: TokenKind, key: str, party: str) -> None:
        self._tokens[(kind, key)].add(party)

    def _add_date(self, day: date, party: str) -> None:
        self._add(TokenKind.DATE, day.isoformat(), party)
        self._add(TokenKind.MONTH_DAY, f"{day.month:02d}-{day.day:02d}", party)

    def find(self, text: str) -> list[Hit]:
        """Every indexed token that appears in `text`, in canonical form."""
        full, month_day = dates_in(text)
        candidates: list[tuple[TokenKind, str]] = [
            *((TokenKind.ID, i) for i in ids_in(text)),
            *((TokenKind.AMOUNT, str(a)) for a in amounts_in(text)),
            *((TokenKind.DATE, d) for d in full),
            *((TokenKind.MONTH_DAY, md) for md in month_day if not any(d.endswith(md) for d in full)),
            *((TokenKind.EMAIL, e) for e in emails_in(text)),
            *((TokenKind.PHONE, p) for p in phones_in(text)),
            *((TokenKind.PHRASE, g) for g in _ngrams(words(text))),
        ]
        hits = [
            Hit(kind, key, frozenset(self._tokens[(kind, key)]))
            for kind, key in candidates
            if (kind, key) in self._tokens
        ]
        padded = f" {' '.join(words(text))} "
        hits += [Hit(TokenKind.NAME, n, frozenset(p)) for n, p in self._names.items() if f" {n} " in padded]
        return hits

    def keys_in(self, text: str) -> set[tuple[TokenKind, str]]:
        """Canonical keys a text mentions, including month-day keys implied by full dates (for "said by caller")."""
        keys = {(h.kind, h.key) for h in self.find(text)}
        _, month_day = dates_in(text)
        keys |= {(TokenKind.MONTH_DAY, md) for md in month_day}
        return keys


def _id_key(value: str) -> str:
    match = _ID.search(value)
    return f"{match.group(1).upper()}-{match.group(2)}" if match else value.strip().upper()


def _ngrams(tokens: list[str]) -> set[str]:
    return {" ".join(tokens[i : i + NGRAM]) for i in range(len(tokens) - NGRAM + 1)}
