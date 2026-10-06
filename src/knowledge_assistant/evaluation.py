import csv
import json
import math
import os
import re
import statistics
import uuid
from pathlib import Path

from .common import canonical_hash, digest, local_path, read_config, read_jsonl, utc_now, write_json, write_jsonl
from .data import load_manifest
from .dify import DifyClient, STATUSES, normalize
from .fixture import SYNTHETIC_BINDING, SYNTHETIC_DOCS, response
from .provenance import SourceIndex

QUESTION_FIELDS = {"question_id", "category", "split", "language", "initial_question", "follow_up_turns", "expected_status",
                   "required_facts", "forbidden_claims", "evidence_doc_ids", "evidence_sections", "reference_answer", "review_status"}
SCORE_FIELDS = ["question_id", "reviewer", "reviewed_at_utc", "correctness", "supported_facts", "checked_facts",
                "unsupported_claim", "correct_abstention", "clarification_success", "retrieval_any", "retrieval_all", "notes"]


def validate_questions(questions, real=True):
    ids, groups = set(), {}
    for q in questions:
        if not QUESTION_FIELDS <= q.keys():
            raise ValueError("Question missing fields")
        if not isinstance(q["question_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", q["question_id"]):
            raise ValueError("Question ID must be a safe ASCII identifier, without path separators")
        if q["question_id"] in ids:
            raise ValueError("Duplicate question_id")
        ids.add(q["question_id"])
        if q["split"] not in {"dev", "holdout"} or q["expected_status"] not in STATUSES:
            raise ValueError("Invalid question split/status")
        if not isinstance(q["initial_question"], str) or not q["initial_question"].strip():
            raise ValueError("Empty question")
        for field in ["follow_up_turns", "required_facts", "forbidden_claims", "evidence_doc_ids", "evidence_sections"]:
            if not isinstance(q[field], list):
                raise ValueError("Question list field invalid")
        if real and q.get("synthetic"):
            raise ValueError("Synthetic question cannot enter real run")
        if real and q["language"] != "en":
            raise ValueError("Official evaluation currently English only")
        if real and q["review_status"] == "reviewed":
            if str(q["reference_answer"]).startswith("DRAFT") or any(str(f).startswith("DRAFT") for f in q["required_facts"]):
                raise ValueError("Reviewed question still contains draft reference/facts")
            if q["expected_status"] in {"answered", "partial"} and (not q["required_facts"] or not q["evidence_doc_ids"]):
                raise ValueError("Reviewed answerable question lacks facts/evidence")
        group = q.get("group_id", q["question_id"])
        if group in groups and groups[group] != q["split"]:
            raise ValueError("Question family crosses split")
        groups[group] = q["split"]


def load_snapshot(snapshot_id, root="."):
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("Set knowledge_snapshot to a local snapshot ID produced by fetch/prepare")
    folder = local_path(root, "data/snapshots/" + snapshot_id)
    meta = json.loads((folder / "snapshot.json").read_text(encoding="utf-8"))
    chunks = read_jsonl(folder / "chunks.jsonl")
    if meta["snapshot_id"] != snapshot_id or canonical_hash(chunks) != meta["chunks_sha256"]:
        raise ValueError("Snapshot integrity failure")
    rows = load_manifest(folder / "documents.csv")
    if canonical_hash(rows) != canonical_hash(meta["documents"]):
        raise ValueError("Snapshot manifest integrity failure")
    if meta.get("format_version") != 2:
        raise ValueError("Legacy snapshot lacks full frozen documents; run prepare for format v2")
    for row in rows:
        if digest((folder / "documents" / f"{row['doc_id']}.md").read_text(encoding="utf-8")) != row["content_sha256"]:
            raise ValueError("Snapshot document integrity failure")
        if digest((folder / "raw" / f"{row['doc_id']}.html").read_bytes()) != row["raw_sha256"]:
            raise ValueError("Snapshot raw integrity failure")
    for c in chunks:
        if digest(c["content"]) != c["content_sha256"] or c["synthetic"]:
            raise ValueError("Invalid real snapshot chunk")
    return meta, rows, chunks


def live_preflight(config, binding, root=".", require_frozen=False):
    if os.environ.get("DIFY_ALLOW_LIVE") != "true":
        raise ValueError("Live calls require DIFY_ALLOW_LIVE=true; no calls made")
    if not os.environ.get("DIFY_BASE_URL") or not os.environ.get("DIFY_APP_API_KEY"):
        raise ValueError("Missing Dify app credentials; no calls made")
    required = ["dify_version", "app_id", "dataset_id", "knowledge_snapshot", "verified_by", "verified_at_utc",
                "verification_evidence", "export_path", "export_sha256"]
    if not binding.get("import_verified") or not binding.get("knowledge_verified") or any(not binding.get(k) for k in required):
        raise ValueError("Actual instance verification/binding incomplete; no calls made")
    if binding["knowledge_snapshot"] != config.get("knowledge_snapshot"):
        raise ValueError("Knowledge binding differs from configured snapshot")
    if not config.get("model_id") or not config.get("embedding_id") or not config.get("model_provider"):
        raise ValueError("Model/embedding config incomplete")
    if digest(local_path(root, binding["export_path"]).read_bytes()) != binding["export_sha256"]:
        raise ValueError("Workflow export differs from verified binding")
    if require_frozen and not config.get("frozen"):
        raise ValueError("Holdout requires frozen config")
    if require_frozen:
        lock = json.loads(local_path(root, f"evaluations/frozen/{config['version']}.json").read_text(encoding="utf-8"))
        state = experiment_state(config, binding, root)
        if lock.get("experiment") != state:
            raise ValueError("Frozen experiment changed")


def experiment_state(config, binding, root="."):
    package_dir = Path(__file__).parent
    implementation = {p.name: digest(p.read_bytes()) for p in sorted(package_dir.glob("*.py"))}
    lock_path = Path(root) / "uv.lock"
    return {"config_sha256": canonical_hash(config), "binding_sha256": canonical_hash(binding),
            "prompt_sha256": digest(local_path(root, config["prompt"]).read_bytes()),
            "query_prompt_sha256": digest(local_path(root, config["query_prompt"]).read_bytes()) if config.get("query_prompt") else None,
            "blueprint_sha256": None if config.get("backend") == "native" else digest(local_path(root, "workflows/chatflow.blueprint.yaml").read_bytes()),
            "implementation_sha256": canonical_hash(implementation),
            "dependency_lock_sha256": digest(lock_path.read_bytes()) if lock_path.exists() else None}


def evaluate(config_path, questions_path, split="dev", mode="mock", root=".", run_id=None, resume=False, limit=None, client=None):
    root = Path(root)
    if mode not in {"mock", "live"}:
        raise ValueError("Invalid run mode")
    config = read_config(config_path)
    native = config.get("backend") == "native"
    questions_all = read_jsonl(questions_path)
    validate_questions(questions_all, real=mode == "live")
    questions = [q for q in questions_all if q["split"] == split]
    if not questions:
        raise ValueError("No questions selected")
    if mode == "live" and any(q["review_status"] != "reviewed" or not q.get("reviewer") or not q.get("reviewed_at_utc") for q in questions):
        raise ValueError("Real evaluation requires human-reviewed questions with reviewer/date")
    if mode == "mock" and (split != "dev" or any(not q.get("synthetic") for q in questions)):
        raise ValueError("Mock can run synthetic dev fixtures only; never real questions or holdout")
    if limit is not None:
        questions = questions[:limit]
    source_index = None
    if mode == "live":
        if native:
            from .native import native_preflight
            binding = native_preflight(config, root, split == "holdout")
        else:
            binding = read_config(root / config["binding"])
            live_preflight(config, binding, root, split == "holdout")
        meta, rows, _ = load_snapshot(config["knowledge_snapshot"], root)
        if binding.get("aggregate_document_ids"):
            # Aggregate UUIDs are never pretended to be a one-to-one document map.
            # SourceIndex.load verifies raw/text/chunk hashes of the full snapshot.
            source_index = SourceIndex.load(config["knowledge_snapshot"], root)
            covered = set(source_index.rows)
        else:
            covered = set(binding["document_map"].values())
        if covered != {r["doc_id"] for r in rows}:
            raise ValueError("Dify document map does not cover exactly the real snapshot")
        if any(not set(q["evidence_doc_ids"]) <= {r["doc_id"] for r in rows} for q in questions):
            raise ValueError("Question evidence outside snapshot")
        if not native:
            client = client or DifyClient(timeout=config["timeout_seconds"], retries=config["max_retries_429"])
        snapshot = meta["snapshot_id"]
    else:
        binding, rows, snapshot = SYNTHETIC_BINDING, SYNTHETIC_DOCS, "synthetic-fixture-v1"
        if native:
            from .chat import DEMO_DOCS, DEMO_SNAPSHOT
            rows, snapshot = DEMO_DOCS, DEMO_SNAPSHOT
            binding = {"backend": "native-demo", "documents_sha256": canonical_hash(rows)}
    identity = {**experiment_state(config, binding, root), "questions_sha256": canonical_hash(questions_all),
                "selected_questions_sha256": canonical_hash(questions), "mode": mode, "split": split,
                "knowledge_snapshot": snapshot, "version": config["version"]}
    if mode == "live" and split == "holdout":
        lock = json.loads((root / f"evaluations/frozen/{config['version']}.json").read_text(encoding="utf-8"))
        if lock.get("questions_sha256") != canonical_hash(questions_all):
            raise ValueError("Question set changed after freeze")
    run_id = run_id or f"{mode}-{config['version']}-{split}-{uuid.uuid4().hex[:10]}"
    folder = local_path(root, "evaluations/runs/" + run_id)
    if mode == "live" and split == "holdout":
        ledger_path = root / "evaluations/holdout-ledger.jsonl"
        ledger = read_jsonl(ledger_path) if ledger_path.exists() else []
        prior = [r for r in ledger if r["version"] == config["version"]]
        if prior and any(r["run_id"] != run_id or r["identity"] != identity for r in prior):
            raise ValueError("Holdout already exposed for this version; resume original run or document a new independent evaluation")
    if folder.exists():
        if not resume:
            raise ValueError("Run already exists; use --resume with matching experiment")
        run_meta = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        if run_meta["identity"] != identity:
            raise ValueError("Resume experiment mismatch")
    else:
        folder.mkdir(parents=True)
        if native and mode == "live":
            (folder / ".gitignore").write_text("*\n", encoding="utf-8")
        write_json(folder / "run.json", {"run_id": run_id, "identity": identity, "started_at_utc": utc_now(),
                   "model_results": mode == "live", "human_scored": False})
        write_json(folder / "config.json", config)
        write_json(folder / "binding.json", binding)
        write_jsonl(folder / "questions.jsonl", questions)
        (folder / "prompt.txt").write_bytes(local_path(root, config["prompt"]).read_bytes())
    if mode == "live" and split == "holdout" and not prior:
        with ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"version": config["version"], "run_id": run_id, "identity": identity, "first_exposure_at": utc_now()}) + "\n")
    done = read_jsonl(folder / "results.jsonl") if (folder / "results.jsonl").exists() else []
    completed = {r["question_id"] for r in done}
    for q in questions:
        if q["question_id"] in completed:
            continue
        turns, conversation = [], None
        if native:
            from .chat import ChatSession
            session = ChatSession("live" if mode == "live" else "demo", config, root,
                trace_dir=folder / "raw" / q["question_id"])
        for turn, query in enumerate([q["initial_question"]] + q["follow_up_turns"]):
            request_id = f"{run_id}/{q['question_id']}/{turn}"
            if native:
                output = session.ask(query)
                output["evaluation_request_id"] = request_id
                output["mode"] = mode
            elif mode == "mock":
                data, error, latency, attempts = response(q["case"], turn), None, None, 0
            else:
                data, error, latency, attempts = client.chat(query, user=run_id + "-" + q["question_id"],
                    conversation_id=conversation, workflow_id=binding.get("workflow_id"))
                if data is not None:
                    safe_response = {k: data.get(k) for k in ["answer", "conversation_id", "message_id", "task_id", "created_at", "mode"]}
                    metadata = data.get("metadata")
                    safe_response["metadata"] = {k: metadata.get(k) for k in ["retriever_resources", "usage"]} if isinstance(metadata, dict) else None
                    write_json(folder / "raw" / f"{q['question_id']}-{turn}.json", safe_response)
            if not native:
                output = normalize(data, request_id, config["version"], snapshot, rows, binding, latency, error, mode, source_index)
                output["attempts"] = attempts
            # Save only response schema fields; never headers, arbitrary metadata or exception text.
            turns.append({"input": query, "turn": turn, "output": output})
            if output["status"] == "error":
                break
            conversation = output.get("conversation_id")
            if turn < len(q["follow_up_turns"]) and not conversation:
                output.update(status="error", error={"code": "missing_conversation_id", "retryable": False})
                break
        row = {"question_id": q["question_id"], "category": q["category"], "turns": turns,
               "completed_at_utc": utc_now()}
        with (folder / "results.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        done.append(row)
    if not native and not (folder / "scores.csv").exists():
        with (folder / "scores.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SCORE_FIELDS)
            writer.writeheader()
            writer.writerows({"question_id": q["question_id"]} for q in questions)
    report(folder)
    return run_id


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator, "value": numerator / denominator if denominator else None}


def summarize(results, questions, scores, mode):
    qmap = {q["question_id"]: q for q in questions}
    rmap = {r["question_id"]: r for r in results}
    if len(qmap) != len(questions) or len(rmap) != len(results) or not set(rmap) <= set(qmap):
        raise ValueError("Invalid result/question IDs")
    failures = sum(any(t["output"]["status"] == "error" for t in r["turns"]) for r in results)
    summary = {"mode": mode, "questions": len(questions), "completed": len(results), "failure_rate": ratio(failures, len(results)),
               "quality_metrics": None, "latency": None, "cost": "unknown"}
    if mode == "mock":
        summary["limitation"] = "Scripted synthetic protocol outputs, no model calls or human scores. No quality comparison allowed."
        return summary
    timings = [t["output"]["latency_ms"] for r in results for t in r["turns"] if t["output"]["latency_ms"] is not None]
    totals = [sum(t["output"]["latency_ms"] for t in r["turns"]) for r in results if all(t["output"]["latency_ms"] is not None for t in r["turns"])]
    def latency(values):
        return {"n": len(values), "median_ms": statistics.median(values) if values else None,
                "p95_ms": sorted(values)[max(0, math.ceil(.95*len(values))-1)] if values else None, "method": "nearest-rank"}
    summary["latency"] = {"single_turn": latency(timings), "question_all_turns": latency(totals)}
    metrics = {k: [0, 0] for k in ["full_correctness", "citation_support", "correct_abstention", "clarification_success",
                                   "unsupported_claim_rate", "retrieval_any", "retrieval_all"]}
    distribution, scored, score_ids = {"0": 0, "1": 0, "2": 0}, 0, set()
    for score in scores:
        qid = score["question_id"]
        if qid in score_ids or qid not in rmap:
            raise ValueError("Duplicate or unknown score question")
        score_ids.add(qid)
        if not score.get("reviewer") or not score.get("reviewed_at_utc"):
            if any(score.get(k) for k in SCORE_FIELDS if k not in {"question_id", "reviewer", "reviewed_at_utc", "notes"}):
                raise ValueError("Human score requires reviewer/date")
            continue
        scored += 1
        q = qmap[qid]
        correct = score.get("correctness", "")
        if correct:
            if correct not in {"0", "1", "2"} or q["expected_status"] not in {"answered", "partial"}:
                raise ValueError("Correctness invalid or not applicable")
            distribution[correct] += 1
            metrics["full_correctness"][0] += correct == "2"
            metrics["full_correctness"][1] += 1
        supported, checked = score.get("supported_facts", ""), score.get("checked_facts", "")
        if supported or checked:
            if not supported.isdigit() or not checked.isdigit() or int(supported) > int(checked):
                raise ValueError("Citation fact counts invalid")
            metrics["citation_support"][0] += int(supported)
            metrics["citation_support"][1] += int(checked)
        for field, metric, applicable in [
            ("unsupported_claim", "unsupported_claim_rate", True),
            ("correct_abstention", "correct_abstention", q["expected_status"] == "insufficient"),
            ("clarification_success", "clarification_success", q["category"] == "ambiguous"),
            ("retrieval_any", "retrieval_any", bool(q["evidence_doc_ids"])),
            ("retrieval_all", "retrieval_all", len(q["evidence_doc_ids"]) > 1)]:
            value = score.get(field, "")
            if value:
                if value not in {"0", "1"} or not applicable:
                    raise ValueError("Invalid binary score/applicability")
                if field.startswith("retrieval") and not any(t["output"].get("retrieved_chunks") is not None for t in rmap[qid]["turns"]):
                    raise ValueError("Retrieval score requires actual full Top-K trace, not citation resources")
                metrics[metric][0] += int(value)
                metrics[metric][1] += 1
    summary.update(human_scored_questions=scored, quality_metrics={k: ratio(*v) for k, v in metrics.items()},
                   correctness_distribution=distribution, unscored_questions=len(results)-scored,
                   limitation="Metrics cover only explicitly reviewed eligible rows; blanks mean unscored, not zero. Failure rate covers completed attempts.")
    return summary


def automatic_summary(results, questions, mode):
    """Engineering/document coverage only; never estimate human answer quality."""
    summary = summarize(results, questions, [], mode)
    for key in ("human_scored_questions", "unscored_questions", "correctness_distribution"):
        summary.pop(key, None)
    summary["quality_metrics"] = None
    summary["evaluation_policy"] = "automated_only"
    summary["human_evaluation"] = {"required": False, "performed": False}
    summary["automatic_document_coverage"] = None
    summary["limitation"] = "Automated engineering checks only. Reference doc_id coverage is not answer correctness or factual support; human scoring is omitted by scope."
    if mode == "mock":
        summary["limitation"] += " Synthetic scripted outputs are not model results."
        return summary
    qmap = {q["question_id"]: q for q in questions}
    eligible, any_hit, all_hit, excluded = 0, 0, 0, 0
    for result in results:
        references = set(qmap[result["question_id"]]["evidence_doc_ids"])
        if not references:
            continue
        traces = [t["output"].get("retrieved_chunks") for t in result["turns"]]
        if not traces or any(trace is None for trace in traces):
            excluded += 1
            continue
        retrieved = {c["doc_id"] for trace in traces for c in trace}
        eligible += 1
        any_hit += bool(references & retrieved)
        all_hit += references <= retrieved
    summary["automatic_document_coverage"] = {
        "any_reference_document": ratio(any_hit, eligible),
        "all_reference_documents": ratio(all_hit, eligible),
        "excluded_missing_trace_questions": excluded,
        "method": "Union of actual full retrieval traces across recorded turns per completed question."}
    return summary


def report(folder):
    folder = Path(folder)
    meta = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    config = read_config(folder / "config.json")
    results, questions = read_jsonl(folder / "results.jsonl"), read_jsonl(folder / "questions.jsonl")
    if config.get("backend") == "native":
        summary = automatic_summary(results, questions, meta["identity"]["mode"])
    else:
        with (folder / "scores.csv").open(encoding="utf-8", newline="") as f:
            scores = list(csv.DictReader(f))
        summary = summarize(results, questions, scores, meta["identity"]["mode"])
    summary["run_id"] = meta["run_id"]
    write_json(folder / "summary.json", summary)
    text = f"# Run {meta['run_id']}\n\nMode: **{summary['mode']}**. Completed {summary['completed']}/{summary['questions']} questions.\n\n"
    text += summary["limitation"] + "\n\n```json\n" + json.dumps(summary, indent=2, ensure_ascii=False) + "\n```\n"
    (folder / "report.md").write_text(text, encoding="utf-8")
    return summary
