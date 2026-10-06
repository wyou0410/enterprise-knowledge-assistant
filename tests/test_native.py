import json
from pathlib import Path

import pytest
import requests

from knowledge_assistant.chat import ChatSession, DEMO_DOCS, compose_messages, validate_answer
from knowledge_assistant.common import canonical_hash, read_config, read_jsonl
from knowledge_assistant.evaluation import automatic_summary, evaluate
from knowledge_assistant.index import build_index, index_identity, load_index, rank, write_index
from knowledge_assistant.providers import ChatModel, Embeddings, ProviderError, load_env


def test_demo_citations_followup_and_new(tmp_path, monkeypatch):
    monkeypatch.setattr(requests.Session, "post", lambda *a, **kw: pytest.fail("demo made network call"))
    s = ChatSession(trace_dir=tmp_path)
    assert s.ask("如何申请电脑？")["status"] == "clarify"
    answer = s.ask("我是员工")
    assert answer["status"] == "answered" and "IT" in answer["answer"]
    assert answer["citations"][0]["excerpt"] == DEMO_DOCS[1]["content"]
    old = s.conversation_id
    s.new()
    assert s.conversation_id != old and not s.history and not s.last_citations
    assert s.ask("每天工作几小时？")["status"] == "insufficient"
    assert len(list(tmp_path.glob("*.json"))) == 3


def test_message_context_budget_and_unknown_citation():
    messages, kept, budget = compose_messages("rules", "question", DEMO_DOCS, [("past", "old answer")], max_chars=500)
    text = messages[-1]["content"]
    assert all(c["content"] in text for c in kept)
    assert budget["dropped_chunk_ids"]
    answer = validate_answer(json.dumps({"status": "answered", "answer": "Invented", "citation_chunk_ids": ["UNKNOWN"]}), kept, "snapshot")
    assert answer["status"] == "error" and answer["citations"] == []
    with pytest.raises(ValueError):
        compose_messages("rules", "x" * 4001, [], [])


def test_index_hash_dimensions_and_stable_ranking(tmp_path):
    identity = {"embedding": {"dimensions": 2}, "snapshot": "synthetic"}
    path = tmp_path / "index.sqlite"
    write_index(path, DEMO_DOCS, [[1, 0], [1, 0]], identity)
    meta, chunks, vectors = load_index(path, identity)
    assert [c["chunk_id"] for c in rank(chunks, vectors, [1, 0])] == sorted(c["chunk_id"] for c in chunks)
    with pytest.raises(ValueError, match="Stale"):
        load_index(path, {**identity, "embedding": {"dimensions": 3}})
    with pytest.raises(ProviderError):
        rank(chunks, vectors, [0, 0])
    import sqlite3
    with sqlite3.connect(path) as db:
        db.execute("UPDATE chunks SET vector='[0,1]' WHERE position=0")
    with pytest.raises(ValueError, match="hash"):
        load_index(path, identity)


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code, self.payload = status, payload
    def json(self):
        return self.payload


class FakeTransport:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []
    def post(self, *a, **kw):
        self.calls.append((a, kw))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def ec():
    return {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "test-model",
            "dimensions": 2, "max_input_chars": 6000, "max_retries_429": 2}


def test_embedding_order_and_explicit_429_retry(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-secret")
    transport = FakeTransport([FakeResponse(429), FakeResponse(payload={"data": [
        {"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}], "usage": {"total_tokens": 3}})])
    vectors, usage = Embeddings(ec(), session=transport, sleep=lambda n: None).embed(["one", "two"])
    assert vectors == [[1, 0], [0, 1]] and len(transport.calls) == 2
    assert transport.calls[0][1]["allow_redirects"] is False


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError(), FakeResponse(500), FakeResponse(302)])
def test_ambiguous_requests_never_retry(monkeypatch, failure):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-secret")
    transport = FakeTransport([failure])
    with pytest.raises(ProviderError):
        Embeddings(ec(), session=transport).embed(["one"])
    assert len(transport.calls) == 1


def test_no_credentials_no_network_and_env_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("NATIVE_ALLOW_LIVE", raising=False)
    with pytest.raises(ValueError, match="Live disabled"):
        ChatSession("live", {})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-secret")
    env = tmp_path / ".env"
    env.write_text('DEEPSEEK_API_KEY="file-secret"\nDASHSCOPE_API_KEY=synthetic\nNATIVE_ALLOW_LIVE=false\nDIFY_BASE_URL=\n', encoding="utf-8")
    load_env(env)
    import os
    assert os.environ["DEEPSEEK_API_KEY"] == "process-secret"
    for endpoint in ["http://api.deepseek.com", "https://evil.test/v1", "https://api.deepseek.com/v1?key=x"]:
        with pytest.raises(ValueError, match="official"):
            ChatModel({"base_url": endpoint, "model": "test"})


def test_native_mock_evaluation_and_resume(tmp_path):
    cfg = read_config("configs/native-baseline.yaml")
    (tmp_path / "prompts").mkdir()
    (tmp_path / cfg["prompt"]).write_text("Synthetic test prompt", encoding="utf-8")
    questions = Path("evaluations/native.synthetic.jsonl")
    run = evaluate("configs/native-baseline.yaml", questions, root=tmp_path, run_id="test")
    folder = tmp_path / "evaluations/runs" / run
    records = read_jsonl(folder / "results.jsonl")
    assert not (folder / "scores.csv").exists()
    assert len(records) == 10
    assert len({r["turns"][0]["output"]["conversation_id"] for r in records}) == 10
    assert json.loads((folder / "summary.json").read_text())["quality_metrics"] is None
    assert evaluate("configs/native-baseline.yaml", questions, root=tmp_path, run_id=run, resume=True) == run
    with pytest.raises(ValueError, match="Mock can"):
        q = read_jsonl(questions)[0]
        q["split"] = "holdout"
        bad = tmp_path / "holdout.jsonl"
        bad.write_text(json.dumps(q) + "\n")
        evaluate("configs/native-baseline.yaml", bad, root=tmp_path, split="holdout")


def test_automatic_report_has_no_human_quality_and_excludes_unknown_retrieval():
    questions = [{"question_id": name, "evidence_doc_ids": ["A", "B"]} for name in ["one", "two", "missing"]]
    results = [{"question_id": name, "turns": [{"output": {"status": "answered", "latency_ms": 1,
        "retrieved_chunks": trace}}]} for name, trace in [
        ("one", [{"doc_id": "A"}, {"doc_id": "B"}]), ("two", [{"doc_id": "A"}]), ("missing", None)]]
    summary = automatic_summary(results, questions, "live")
    assert summary["quality_metrics"] is None
    assert summary["human_evaluation"] == {"required": False, "performed": False}
    assert "unscored_questions" not in summary
    coverage = summary["automatic_document_coverage"]
    assert coverage["all_reference_documents"] == {"numerator": 1, "denominator": 2, "value": .5}
    assert coverage["excluded_missing_trace_questions"] == 1


def live_fixture(tmp_path, monkeypatch):
    cfg = read_config("configs/native-baseline.yaml")
    cfg["chat_model"]["model"] = "synthetic-test-model"
    cfg["embedding"] = ec()
    monkeypatch.setenv("NATIVE_ALLOW_LIVE", "true")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "synthetic-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    monkeypatch.setattr("knowledge_assistant.evaluation.load_snapshot", lambda *a: ({}, [], DEMO_DOCS))
    (tmp_path / "prompts").mkdir()
    (tmp_path / cfg["prompt"]).write_text("Synthetic system rules", encoding="utf-8")
    write_index(tmp_path / cfg["index_path"], DEMO_DOCS, [[1, 0], [0, 1]], index_identity(cfg, DEMO_DOCS))
    return cfg


def test_live_core_actual_messages_citations_and_pre_request_trace(tmp_path, monkeypatch):
    cfg = live_fixture(tmp_path, monkeypatch)
    class FakeEmbedding:
        def embed(self, texts):
            return [[1, 0]], {"total_tokens": 1}
    class FakeModel:
        def complete(self, messages):
            trace = json.loads(next((tmp_path / "logs").glob("*.json")).read_text(encoding="utf-8"))
            assert trace["state"] == "before_model_request"
            assert trace["messages"] == messages
            payload = json.loads(messages[-1]["content"])
            assert payload["retrieved_untrusted_documents"][0]["content"] == DEMO_DOCS[0]["content"]
            return json.dumps({"status": "answered", "answer": "Synthetic test answer",
                "citation_chunk_ids": ["DEMO001-0001"]}), {"total_tokens": 2}
    session = ChatSession("live", cfg, tmp_path, tmp_path / "logs", FakeEmbedding(), FakeModel())
    answer = session.ask("Synthetic test question")
    assert answer["status"] == "answered" and answer["citations"][0]["doc_id"] == "DEMO001"
    assert len(answer["retrieved_chunks"]) == 2
    trace = json.loads(next((tmp_path / "logs").glob("*.json")).read_text(encoding="utf-8"))
    assert trace["messages_sha256"] == canonical_hash(trace["messages"])
    assert trace["state"] == "completed" and trace["cost"] == "unknown"


def test_embedding_cache_and_native_frozen_hash_gate(tmp_path, monkeypatch):
    cfg = live_fixture(tmp_path, monkeypatch)
    class FakeEmbedding:
        calls = 0
        def embed(self, texts):
            self.calls += 1
            return [[1, 0] for _ in texts], None
    fake = FakeEmbedding()
    first = build_index(cfg, tmp_path, fake)
    second = build_index(cfg, tmp_path, fake)
    assert first["requests"] == 1 and first["embedded_chunks"] == 2 and second["requests"] == 0 and fake.calls == 1
    from knowledge_assistant.native import native_preflight
    from knowledge_assistant.evaluation import experiment_state
    with pytest.raises(ValueError, match="frozen"):
        native_preflight(cfg, tmp_path, require_frozen=True)
    cfg["frozen"] = True
    binding = native_preflight(cfg, tmp_path)
    folder = tmp_path / "evaluations/frozen"
    folder.mkdir(parents=True)
    (folder / "native-baseline.json").write_text(json.dumps({"experiment": experiment_state(cfg, binding, tmp_path)}))
    assert native_preflight(cfg, tmp_path, require_frozen=True) == binding
    (tmp_path / cfg["prompt"]).write_text("Changed system rules")
    with pytest.raises(ValueError, match="changed"):
        native_preflight(cfg, tmp_path, require_frozen=True)


def test_failed_query_embedding_has_no_claimed_retrieval_trace(tmp_path, monkeypatch):
    cfg = live_fixture(tmp_path, monkeypatch)
    class FailedEmbedding:
        def embed(self, texts):
            raise ProviderError("timeout_completion_unknown")
    session = ChatSession("live", cfg, tmp_path, embedder=FailedEmbedding(), model=object())
    result = session.ask("Synthetic question")
    assert result["status"] == "error" and result["retrieved_chunks"] is None
