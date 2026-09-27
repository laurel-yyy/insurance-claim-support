"""Deterministic retrieval and rendering of document guidance and follow-up topics (K1-K7).

The LLM only paraphrases what these functions return. Nothing here reads free text from the LLM except the
topic names and the caller's raw message, which are matched against data-driven lists.
"""

import re
import string
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from sop_agent.domain.enums import Path
from sop_agent.domain.models import Claim, DocumentGuideline, FollowupTopic

DEFAULT_ALTERNATIVE_KEY = "default"


def _doc_tokens(name: str) -> tuple[str, ...]:
    words = re.findall(r"[a-z0-9]+", name.casefold())
    return tuple(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words)


def match_document(name: str, keys: Iterable[str]) -> str | None:
    """K1: exact normalized match first, else the single key whose tokens contain all of the name's tokens."""
    wanted = _doc_tokens(name)
    if not wanted:
        return None
    key_list = [k for k in keys if k != DEFAULT_ALTERNATIVE_KEY]
    for key in key_list:
        if _doc_tokens(key) == wanted:
            return key
    supersets = [k for k in key_list if set(wanted) <= set(_doc_tokens(k))]
    return supersets[0] if len(supersets) == 1 else None


@dataclass(frozen=True)
class DocumentGuidance:
    name: str
    matched_key: str | None
    requirements: str | None
    alternatives: str


@dataclass(frozen=True)
class DocumentBundle:
    """K2: everything the caller needs to submit this claim's documents."""

    case_id: str
    documents: tuple[DocumentGuidance, ...]
    case_type_guidance: str | None
    default_guidance: str
    settings: Mapping[str, str]


def document_bundle(claim: Claim, guideline: DocumentGuideline) -> DocumentBundle:
    docs = []
    for name in claim.documents_needed:
        key = match_document(name, guideline.document_guidance)
        docs.append(
            DocumentGuidance(
                name=name,
                matched_key=key,
                requirements=guideline.document_guidance.get(key) if key else None,
                alternatives=_alternatives_for(key, guideline),
            )
        )
    return DocumentBundle(
        case_id=claim.case_id,
        documents=tuple(docs),
        case_type_guidance=guideline.case_type_guidance.get(claim.case_type.casefold()),
        default_guidance=guideline.default_guidance,
        settings=guideline.settings,
    )


def _alternatives_for(key: str | None, guideline: DocumentGuideline) -> str:
    alternatives = guideline.document_alternative_guidance
    if key and key in alternatives:
        return alternatives[key]
    return alternatives.get(DEFAULT_ALTERNATIVE_KEY, guideline.default_guidance)


def match_claim_document(mention: str, claim: Claim) -> str | None:
    """Map a document the caller mentioned ("the repair estimate") to one of the claim's documents."""
    return match_document(mention, claim.documents_needed)


def join_documents(names: Sequence[str]) -> str:
    """K5: "the repair estimate and the police report"."""
    items = [f"the {n}" for n in names]
    if not items:
        return "the requested documents"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def render_template(template: str, claim: Claim, settings: Mapping[str, str]) -> str | None:
    """K5: fill known placeholders; any unknown placeholder means the topic is skipped (returns None)."""
    values = {"case_id": claim.case_id, "documents": join_documents(claim.documents_needed), **settings}
    try:
        fields = [name for _, name, _, _ in string.Formatter().parse(template) if name is not None]
    except ValueError:
        return None
    if any(name not in values for name in fields):
        return None
    return template.format_map(values)


@dataclass(frozen=True)
class RenderedTopic:
    topic: str
    text: str


@dataclass(frozen=True)
class TopicSelection:
    triggered: tuple[RenderedTopic, ...] = ()
    background: tuple[RenderedTopic, ...] = ()
    fallback: str | None = None
    skipped: tuple[str, ...] = field(default_factory=tuple)


def normalize_words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.casefold()))


def _phrase_hit(topic: FollowupTopic, words: str) -> bool:
    padded = f" {words} "
    return any(f" {normalize_words(p)} " in padded for p in topic.match_any)


def select_topics(
    guideline: DocumentGuideline,
    claim: Claim,
    intents: set[Path],
    nlu_topics: Iterable[str],
    caller_text: str,
    followup_asked: bool,
) -> TopicSelection:
    """K3 filter, K4 trigger, K5 render, K6 fallback."""
    requested = {t.strip().casefold() for t in nlu_topics}
    words = normalize_words(caller_text)
    intent_values = {p.value for p in intents}
    triggered: list[RenderedTopic] = []
    background: list[RenderedTopic] = []
    skipped: list[str] = []
    for topic in guideline.followup_topics:
        if topic.requires_documents and not claim.documents_needed:
            continue
        if not intent_values & set(topic.intent_hints):
            continue
        is_background = not topic.match_any
        if not is_background and topic.topic.casefold() not in requested and not _phrase_hit(topic, words):
            continue
        text = render_template(topic.template, claim, guideline.settings)
        if text is None:
            skipped.append(topic.topic)
            continue
        (background if is_background else triggered).append(RenderedTopic(topic.topic, text))
    fallback = guideline.followup_fallback if followup_asked and not triggered else None
    return TopicSelection(tuple(triggered), tuple(background), fallback, tuple(skipped))


def topic_names(guideline: DocumentGuideline) -> tuple[str, ...]:
    """K7: the Extractor's followup_topics enum, generated from the loaded data."""
    return tuple(t.topic for t in guideline.followup_topics)
