import json
from pathlib import Path

import pytest
import yaml

from knowledge_assistant.common import read_jsonl
from knowledge_assistant.evaluation import evaluate, live_preflight, summarize, validate_questions


def test_question_family_cannot_cross_split():
    q = read_jsonl("evaluations/smoke.synthetic.jsonl")[:2]
    q[0]["group_id"] = q[1]["group_id"] = "same"
    q[1]["split"] = "holdout"
    with pytest.raises(ValueError, match="crosses"):
        validate_questions(q, real=False)


def test_real_questions_cannot_be_run_as_mock():
    with pytest.raises(ValueError, match="Synthetic question"):
        validate_questions(read_jsonl("evaluations/smoke.synthetic.jsonl"))


def test_flipping_review_status_does_not_remove_draft_reference():
    q = read_jsonl("evaluations/smoke.synthetic.jsonl")[0]
    q.update(synthetic=False, review_status="reviewed", reference_answer="DRAFT evidence passage")
    with pytest.raises(ValueError, match="draft reference"):
        validate_questions([q])


def test_live_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DIFY_ALLOW_LIVE", raising=False)
    with pytest.raises(ValueError, match="Live calls"):
        live_preflight({}, {})


def test_metric_denominators_by_hand():
    questions = [
        {"question_id": "A", "expected_status": "answered", "category": "single", "evidence_doc_ids": ["DOC-1"]},
        {"question_id": "B", "expected_status": "partial", "category": "partial", "evidence_doc_ids": ["DOC-1"]},
        {"question_id": "C", "expected_status": "insufficient", "category": "unanswerable", "evidence_doc_ids": []},
        {"question_id": "D", "expected_status": "answered", "category": "ambiguous", "evidence_doc_ids": ["DOC-1"]},
    ]
    results = [{"question_id": q["question_id"], "turns": [{"output": {"status": "answered", "latency_ms": n * 100, "retrieved_chunks": None}}]} for n,q in enumerate(questions,1)]
    scores = [{"question_id": "A", "reviewer": "fixture-tester", "reviewed_at_utc": "fixture-time", "correctness": "2", "supported_facts": "2", "checked_facts": "3", "unsupported_claim": "1"},
              {"question_id": "B", "reviewer": "fixture-tester", "reviewed_at_utc": "fixture-time", "correctness": "1", "supported_facts": "1", "checked_facts": "1", "unsupported_claim": "0"},
              {"question_id": "C", "reviewer": "fixture-tester", "reviewed_at_utc": "fixture-time", "correct_abstention": "1"},
              {"question_id": "D", "reviewer": "fixture-tester", "reviewed_at_utc": "fixture-time", "clarification_success": "0"}]
    summary = summarize(results, questions, scores, "live")
    m = summary["quality_metrics"]
    assert m["full_correctness"] == {"numerator": 1, "denominator": 2, "value": .5}
    assert m["citation_support"]["value"] == .75 and m["citation_support"]["denominator"] == 4
    assert m["correct_abstention"]["denominator"] == 1
    assert m["unsupported_claim_rate"]["denominator"] == 2
    assert summary["latency"]["single_turn"]["median_ms"] == 250
    assert summary["latency"]["single_turn"]["p95_ms"] == 400
    assert summary["correctness_distribution"] == {"0": 0, "1": 1, "2": 1}


def test_unscored_is_not_zero_and_mock_has_no_quality():
    q = [{"question_id": "A", "expected_status": "answered", "category": "single", "evidence_doc_ids": []}]
    r = [{"question_id": "A", "turns": [{"output": {"status": "answered", "latency_ms": None}}]}]
    summary = summarize(r, q, [{"question_id": "A", "reviewer": ""}], "live")
    assert summary["quality_metrics"]["full_correctness"]["value"] is None
    assert summarize(r, q, [], "mock")["quality_metrics"] is None


def test_no_fake_human_scores():
    q = [{"question_id": "A", "expected_status": "answered", "category": "single", "evidence_doc_ids": []}]
    r = [{"question_id": "A", "turns": [{"output": {"status": "answered", "latency_ms": None}}]}]
    with pytest.raises(ValueError, match="reviewer/date"):
        summarize(r, q, [{"question_id": "A", "correctness": "2"}], "live")


def test_resume_hash_gate_and_mock_artifacts(tmp_path):
    (tmp_path / "prompts").mkdir(); (tmp_path / "workflows").mkdir()
    (tmp_path / "prompts/p.txt").write_text("fixture prompt")
    (tmp_path / "workflows/chatflow.blueprint.yaml").write_text("fixture blueprint")
    cfg = {"version": "baseline", "prompt": "prompts/p.txt"}
    cfg_path = tmp_path / "config.yaml"; cfg_path.write_text(yaml.safe_dump(cfg))
    run_id = evaluate(cfg_path, "evaluations/smoke.synthetic.jsonl", root=tmp_path, run_id="fixture-test")
    folder = tmp_path / "evaluations/runs" / run_id
    assert len(read_jsonl(folder / "results.jsonl")) == 10
    assert evaluate(cfg_path, "evaluations/smoke.synthetic.jsonl", root=tmp_path, run_id=run_id, resume=True) == run_id
    assert len(read_jsonl(folder / "results.jsonl")) == 10
    assert json.loads((folder / "summary.json").read_text())["quality_metrics"] is None
    (tmp_path / "prompts/p.txt").write_text("changed")
    with pytest.raises(ValueError, match="mismatch"):
        evaluate(cfg_path, "evaluations/smoke.synthetic.jsonl", root=tmp_path, run_id=run_id, resume=True)


def test_holdout_never_runs_as_fixture(tmp_path):
    questions = read_jsonl("evaluations/smoke.synthetic.jsonl")
    questions[0]["split"] = "holdout"
    path = tmp_path / "questions.jsonl"
    path.write_text("\n".join(json.dumps(q) for q in questions))
    with pytest.raises(ValueError, match="holdout"):
        evaluate("configs/native-baseline.yaml", path, split="holdout", mode="mock")
