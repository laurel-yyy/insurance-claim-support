from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import Path
from sop_agent.domain.models import Claim, FollowupTopic
from sop_agent.sop.guidance import (
    document_bundle,
    join_documents,
    match_claim_document,
    match_document,
    render_template,
    select_topics,
    topic_names,
)


def _claim(repo: InMemoryRepository, case_id: str) -> Claim:
    claim = repo.claim(case_id)
    assert claim is not None
    return claim


def test_k1_starter_document_names_map_to_guidance_keys(snapshot_repo: InMemoryRepository) -> None:
    keys = snapshot_repo.document_guideline().document_guidance
    assert match_document("pathology report", keys) == "original pathology report"
    assert match_document("office note", keys) == "treating provider office note"


def test_k1_diagnosis_report_has_no_dedicated_guidance(snapshot_repo: InMemoryRepository) -> None:
    assert match_document("diagnosis report", snapshot_repo.document_guideline().document_guidance) is None


def test_k1_exact_match_wins_over_superset() -> None:
    keys = ["office note", "treating provider office note"]
    assert match_document("Office Note", keys) == "office note"


def test_k1_ambiguous_superset_returns_none() -> None:
    assert match_document("office note", ["treating provider office note", "specialist office note"]) is None


def test_k1_singularizes_plurals_and_strips_punctuation() -> None:
    assert match_document("Repair Estimates", ["repair estimate"]) == "repair estimate"
    assert match_document("accident-scene photos", ["supplemental accident scene photos"]) == (
        "supplemental accident scene photos"
    )
    assert match_document("glass", ["glass"]) == "glass"  # "ss" is not a plural


def test_k1_default_alternative_key_is_never_a_document_match() -> None:
    assert match_document("default", ["default"]) is None


def test_k2_bundle_uses_document_and_default_alternatives(snapshot_repo: InMemoryRepository) -> None:
    guideline = snapshot_repo.document_guideline()
    bundle = document_bundle(_claim(snapshot_repo, "CL-2048"), guideline)
    assert [d.matched_key for d in bundle.documents] == [
        "original pathology report",
        "treating provider office note",
    ]
    assert all(d.requirements for d in bundle.documents)
    assert (
        bundle.documents[0].alternatives
        == guideline.document_alternative_guidance["original pathology report"]
    )
    assert bundle.case_type_guidance == guideline.case_type_guidance["healthcare"]
    general = document_bundle(_claim(snapshot_repo, "CL-3001"), guideline)
    [diagnosis] = general.documents
    assert (diagnosis.matched_key, diagnosis.requirements) == (None, None)
    assert diagnosis.alternatives == guideline.document_alternative_guidance["default"]


def test_k2_each_ambiguous_candidate_gets_its_own_bundle(merged_repo: InMemoryRepository) -> None:
    guideline = merged_repo.document_guideline()
    first = document_bundle(_claim(merged_repo, "CL-9101"), guideline)
    second = document_bundle(_claim(merged_repo, "CL-9102"), guideline)
    assert first != second
    assert first.case_id == "CL-9101" and second.case_id == "CL-9102"
    assert [d.matched_key for d in first.documents] == [None, "treating provider office note"]
    assert [d.matched_key for d in second.documents] == [None]


def test_caller_mention_maps_to_claim_document(snapshot_repo: InMemoryRepository) -> None:
    claim = _claim(snapshot_repo, "CL-2048")
    assert (
        match_claim_document("the original pathology report", claim) is None
    )  # more specific than the claim
    assert match_claim_document("pathology report", claim) == "pathology report"
    assert match_claim_document("office notes", claim) == "office note"


def test_k5_documents_join_naturally() -> None:
    assert join_documents(["pathology report", "office note"]) == "the pathology report and the office note"
    assert join_documents(["a", "b", "c"]) == "the a, the b, and the c"
    assert join_documents(["x"]) == "the x"


def test_k5_placeholders_render_from_claim_and_settings(snapshot_repo: InMemoryRepository) -> None:
    guideline = snapshot_repo.document_guideline()
    claim = _claim(snapshot_repo, "CL-2048")
    text = render_template(
        "{case_id}: {documents}, {average_processing_time_after_submission}", claim, guideline.settings
    )
    assert text == "CL-2048: the pathology report and the office note, usually less than a week"


def test_k5_unknown_placeholder_skips_the_topic(snapshot_repo: InMemoryRepository) -> None:
    guideline = snapshot_repo.document_guideline()
    claim = _claim(snapshot_repo, "CL-2048")
    assert render_template("For {case_id}, see {portal_url}.", claim, guideline.settings) is None
    assert render_template("Broken {", claim, guideline.settings) is None
    bad = FollowupTopic(
        topic="bad",
        intent_hints=("document_submission",),
        requires_documents=False,
        match_any=("portal",),
        template="{nope}",
    )
    patched = guideline.model_copy(update={"followup_topics": (*guideline.followup_topics, bad)})
    selection = select_topics(patched, claim, {Path.DOCUMENT_SUBMISSION}, ["bad"], "", followup_asked=True)
    assert "bad" in selection.skipped
    assert "bad" not in {t.topic for t in selection.triggered}


def test_k4_topics_trigger_by_nlu_topic_and_by_phrase(snapshot_repo: InMemoryRepository) -> None:
    guideline = snapshot_repo.document_guideline()
    claim = _claim(snapshot_repo, "CL-2048")
    by_nlu = select_topics(
        guideline, claim, {Path.DOCUMENT_SUBMISSION}, ["submission_method"], "", followup_asked=True
    )
    assert [t.topic for t in by_nlu.triggered] == ["submission_method"]
    by_phrase = select_topics(
        guideline, claim, {Path.DOCUMENT_SUBMISSION}, [], "How long does it take after I send it?", True
    )
    assert "processing_time_after_submission" in {t.topic for t in by_phrase.triggered}
    assert by_phrase.fallback is None


def test_k3_topics_need_a_matching_intent(snapshot_repo: InMemoryRepository) -> None:
    guideline = snapshot_repo.document_guideline()
    claim = _claim(snapshot_repo, "CL-2048")
    selection = select_topics(guideline, claim, {Path.STATUS_INQUIRY}, ["submission_method"], "portal", True)
    assert "submission_method" not in {
        t.topic for t in selection.triggered
    }  # intent_hints: document_submission


def test_k4_topic_without_phrases_is_background(snapshot_repo: InMemoryRepository) -> None:
    guideline = snapshot_repo.document_guideline()
    selection = select_topics(
        guideline, _claim(snapshot_repo, "CL-2048"), {Path.DENIAL_QUESTION}, [], "", False
    )
    assert [t.topic for t in selection.background] == ["missing_required_material_alternatives"]
    assert "CL-2048" in selection.background[0].text


def test_k3_requires_documents_topics_skip_claims_without_documents(
    snapshot_repo: InMemoryRepository,
) -> None:
    guideline = snapshot_repo.document_guideline()
    selection = select_topics(
        guideline,
        _claim(snapshot_repo, "CL-2011"),
        set(Path),
        topic_names(guideline),
        "how long portal",
        True,
    )
    assert selection.triggered == () and selection.background == ()
    assert selection.fallback == guideline.followup_fallback


def test_k6_fallback_only_when_a_followup_was_asked_and_nothing_triggered(
    snapshot_repo: InMemoryRepository,
) -> None:
    guideline = snapshot_repo.document_guideline()
    claim = _claim(snapshot_repo, "CL-2048")
    asked = select_topics(
        guideline, claim, {Path.DOCUMENT_SUBMISSION}, [], "who reviews it?", followup_asked=True
    )
    assert asked.fallback == guideline.followup_fallback
    silent = select_topics(
        guideline, claim, {Path.DOCUMENT_SUBMISSION}, [], "who reviews it?", followup_asked=False
    )
    assert silent.fallback is None


def test_k7_topic_names_come_from_data(snapshot_repo: InMemoryRepository) -> None:
    names = topic_names(snapshot_repo.document_guideline())
    assert len(names) == 6
    assert "submission_method" in names
