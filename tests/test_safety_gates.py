import json
from pathlib import Path

import pytest

from knowledge_assistant.common import canonical_hash, digest, write_json
from knowledge_assistant.dify import normalize
from knowledge_assistant.evaluation import experiment_state, live_preflight, summarize
from knowledge_assistant.fixture import response, SYNTHETIC_BINDING, SYNTHETIC_DOCS


def test_structured_answer_requires_actual_resources():
    data = {"answer": json.dumps({"status": "answered", "answer": "A fabricated rule", "citation_doc_ids": []}), "metadata": {}}
    out = normalize(data, "test/q", "fixture", "fixture", SYNTHETIC_DOCS, SYNTHETIC_BINDING)
    assert out["status"] == "error" and out["error"]["code"] == "missing_evidence_resources"


def test_contract_cannot_cite_unretrieved_doc():
    data = response("answered", 0)
    parsed = json.loads(data["answer"]); parsed["citation_doc_ids"] = ["SYN-003"]
    data["answer"] = json.dumps(parsed)
    out = normalize(data, "test/q", "fixture", "fixture", SYNTHETIC_DOCS, SYNTHETIC_BINDING)
    assert out["error"]["code"] == "citation_not_in_resources"


def test_retrieval_metric_does_not_use_citation_resources():
    q = [{"question_id": "A", "expected_status": "answered", "category": "single", "evidence_doc_ids": ["D"]}]
    r = [{"question_id": "A", "turns": [{"output": {"status": "answered", "latency_ms": 1, "retrieved_chunks": None, "citations": [{"doc_id": "D"}]}}]}]
    with pytest.raises(ValueError, match="Top-K"):
        summarize(r, q, [{"question_id": "A", "reviewer": "fixture", "reviewed_at_utc": "fixture", "retrieval_any": "1"}], "live")


def test_freeze_detects_prompt_export_and_config_change(tmp_path, monkeypatch):
    # All IDs and credentials below are fixtures; no live request is made.
    monkeypatch.setenv("DIFY_ALLOW_LIVE", "true")
    monkeypatch.setenv("DIFY_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DIFY_APP_API_KEY", "fixture")
    (tmp_path / "prompts").mkdir(); (tmp_path / "workflows").mkdir()
    (tmp_path / "prompts/p.txt").write_text("prompt")
    (tmp_path / "workflows/chatflow.blueprint.yaml").write_text("blueprint")
    export = tmp_path / "workflows/export.yaml"; export.write_text("fixture DSL")
    cfg = {"version": "fixture", "prompt": "prompts/p.txt", "knowledge_snapshot": "fixture", "model_id": "fixture", "embedding_id": "fixture", "model_provider": "fixture", "frozen": True}
    binding = {k: "fixture" for k in ["dify_version", "app_id", "dataset_id", "knowledge_snapshot", "verified_by", "verified_at_utc", "verification_evidence"]}
    binding.update(import_verified=True, knowledge_verified=True, export_path="workflows/export.yaml", export_sha256=digest(export.read_bytes()))
    write_json(tmp_path / "evaluations/frozen/fixture.json", {"experiment": experiment_state(cfg, binding, tmp_path)})
    live_preflight(cfg, binding, tmp_path, True)
    (tmp_path / "prompts/p.txt").write_text("changed")
    with pytest.raises(ValueError, match="Frozen experiment changed"):
        live_preflight(cfg, binding, tmp_path, True)
    export.write_text("changed")
    with pytest.raises(ValueError, match="export differs"):
        live_preflight(cfg, binding, tmp_path)


def test_insecure_remote_endpoint_refused():
    from knowledge_assistant.dify import DifyClient
    data, error, _, attempts = DifyClient("http://remote.example.invalid/v1", "fixture").chat("q", "u")
    assert error["code"] == "insecure_base_url" and attempts == 0
