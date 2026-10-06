import copy
import json

import pytest

from knowledge_assistant.common import canonical_hash, write_json, write_jsonl
from knowledge_assistant.review import apply_receipt, EDIT_FIELDS, render


def fixture(tmp_path, monkeypatch):
    # Synthetic fixtures test receipt gates; they are never human study evidence.
    q = {"question_id": "F001", "category": "single", "split": "dev", "language": "en",
         "initial_question": "Fixture question", "follow_up_turns": [], "expected_status": "answered",
         "expected_initial_status": "answered", "required_facts": ["DRAFT pending"],
         "forbidden_claims": [], "evidence_doc_ids": ["A"], "evidence_sections": ["Policy"],
         "evidence_chunk_ids": ["A-1"], "reference_answer": "DRAFT pending", "review_status": "draft",
         "group_id": "family", "knowledge_snapshot": "fixture", "synthetic": False}
    write_jsonl(tmp_path / "evaluations/questions.jsonl", [q])
    candidate = dict(q, required_facts=["Fixture approved fact"], reference_answer="Fixture answer")
    packet = {"format_version": 1, "split": "dev", "snapshot": "fixture", "base_questions_sha256": canonical_hash([q]),
              "items": [{"original": q, "candidate": candidate, "evidence": [], "review_focus": "Fixture"}]}
    packet["packet_sha256"] = canonical_hash(packet)
    write_json(tmp_path / "packet.json", packet)
    receipt = {"format_version": 1, "split": "dev", "snapshot": "fixture", "base_questions_sha256": canonical_hash([q]),
               "packet_sha256": packet["packet_sha256"], "human_attestation": True,
               "reviewer": "SYNTHETIC TEST REVIEWER", "reviewed_at_utc": "2026-01-01T00:00:00Z",
               "reviews": [{"question_id": "F001", "decision": "approve", "fields": {k: candidate[k] for k in EDIT_FIELDS}, "notes": "Synthetic receipt"}]}
    monkeypatch.setattr("knowledge_assistant.review.load_snapshot", lambda *a: ({}, [{"doc_id": "A"}], [{"chunk_id": "A-1", "doc_id": "A", "section_path": "Policy"}]))
    return q, packet, receipt


def apply(tmp_path, receipt):
    write_json(tmp_path / "receipt.json", receipt)
    return apply_receipt(tmp_path / "receipt.json", tmp_path / "packet.json", tmp_path)


def test_explicit_receipt_writes_new_set_without_overwriting_original(tmp_path, monkeypatch):
    q, packet, receipt = fixture(tmp_path, monkeypatch)
    before = (tmp_path / "evaluations/questions.jsonl").read_bytes()
    result = apply(tmp_path, receipt)
    assert result["approved"] == ["F001"]
    assert (tmp_path / "evaluations/questions.jsonl").read_bytes() == before
    reviewed = json.loads((tmp_path / "evaluations/questions.reviewed.jsonl").read_text())
    assert reviewed["reviewer"] == "SYNTHETIC TEST REVIEWER" and reviewed["review_status"] == "reviewed"


@pytest.mark.parametrize("change", ["missing_attestation", "missing_reviewer", "stale_hash", "draft_answer", "unknown_evidence", "duplicate_id"])
def test_invalid_receipts_do_not_modify_question_set(tmp_path, monkeypatch, change):
    _, _, receipt = fixture(tmp_path, monkeypatch)
    if change == "missing_attestation": receipt["human_attestation"] = False
    if change == "missing_reviewer": receipt["reviewer"] = ""
    if change == "stale_hash": receipt["base_questions_sha256"] = "stale"
    if change == "draft_answer": receipt["reviews"][0]["fields"]["reference_answer"] = "DRAFT pending"
    if change == "unknown_evidence": receipt["reviews"][0]["fields"]["evidence_chunk_ids"] = ["unknown"]
    if change == "duplicate_id": receipt["reviews"].append(copy.deepcopy(receipt["reviews"][0]))
    with pytest.raises(ValueError): apply(tmp_path, receipt)
    assert not (tmp_path / "evaluations/questions.reviewed.jsonl").exists()


def test_review_page_has_no_auto_approval_and_escapes_evidence(tmp_path, monkeypatch):
    _, packet, _ = fixture(tmp_path, monkeypatch)
    packet["items"][0]["evidence"] = [{"chunk_id": "F", "section_path": "Fake", "source_url": "https://example.invalid/",
                                      "content_sha256": "fixture", "content": "</script><script>alert('fixture')</script>"}]
    render(tmp_path / "review.html", packet)
    page = (tmp_path / "review.html").read_text(encoding="utf-8")
    assert '<option value="pending">' in page and 'selected>我已核对' not in page
    assert "</script><script>alert" not in page and "&lt;/script&gt;" in page
    assert "fetch(" not in page
