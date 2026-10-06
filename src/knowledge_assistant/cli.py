import argparse
import json
import sys
from pathlib import Path

import yaml

from .common import canonical_hash, digest, read_config, read_jsonl, utc_now, write_json
from .data import fetch, load_manifest, prepare, validate_data
from .evaluation import evaluate, experiment_state, live_preflight, load_snapshot, report, validate_questions
from .questions import draft


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local knowledge CLI; demo uses original synthetic MOCK documents. Public CLI uses native retrieval.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("chat"); p.add_argument("--mode", choices=["demo", "live"], default="demo"); p.add_argument("--config", default="configs/native-baseline.yaml"); p.add_argument("--env-file"); p.add_argument("--trace-dir", default="data/native/chat-logs")
    p = sub.add_parser("build-index"); p.add_argument("--config", default="configs/native-baseline.yaml"); p.add_argument("--env-file")
    p = sub.add_parser("fetch"); p.add_argument("--config", default="configs/sources.yaml")
    p = sub.add_parser("prepare"); p.add_argument("--manifest", default="data/manifests/documents.csv"); p.add_argument("--max-chars", type=int, default=2400); p.add_argument("--overlap-chars", type=int, default=200)
    p = sub.add_parser("validate-data"); p.add_argument("--manifest", default="data/manifests/documents.csv")
    p = sub.add_parser("draft-questions"); p.add_argument("--snapshot", required=True)
    p = sub.add_parser("validate-questions"); p.add_argument("--questions", default="evaluations/questions.jsonl")
    p = sub.add_parser("evaluate"); p.add_argument("--version", choices=["native-baseline", "native-improved"], default="native-baseline"); p.add_argument("--split", choices=["dev", "holdout"], default="dev"); p.add_argument("--mode", choices=["mock", "live"], default="mock"); p.add_argument("--questions"); p.add_argument("--run-id"); p.add_argument("--resume", action="store_true"); p.add_argument("--limit", type=int); p.add_argument("--env-file")
    p = sub.add_parser("report"); p.add_argument("--run-dir", required=True)
    p = sub.add_parser("export-knowledge"); p.add_argument("--snapshot", required=True)
    p = sub.add_parser("freeze"); p.add_argument("--version", choices=["native-baseline", "native-improved"], required=True); p.add_argument("--reviewer", required=True); p.add_argument("--questions", default="evaluations/questions.jsonl"); p.add_argument("--env-file")
    args = parser.parse_args(argv)
    try:
        if getattr(args, "env_file", None):
            from .providers import load_env
            load_env(args.env_file)
        if args.command == "chat":
            from .chat import ChatSession, terminal
            return terminal(ChatSession(args.mode, read_config(args.config) if args.mode == "live" else None, trace_dir=args.trace_dir))
        elif args.command == "build-index":
            from .index import build_index
            cfg = read_config(args.config)
            # No request is made until configuration, authorization and snapshot pass validation.
            _, _, chunks = load_snapshot(cfg["knowledge_snapshot"])
            import math
            print(f"Live indexing: {len(chunks)} chunks; up to {math.ceil(len(chunks)/cfg['embedding'].get('batch_size',8))} batches, cache reused. Cost unknown.")
            result = build_index(cfg)
        elif args.command == "fetch":
            rows = fetch(args.config)
            result = {"included": sum(r["included"] == "true" for r in rows), "candidates": len(rows)}
        elif args.command == "prepare":
            snapshot, count = prepare(args.manifest, max_chars=args.max_chars, overlap_chars=args.overlap_chars)
            result = {"snapshot": snapshot, "chunks": count}
        elif args.command == "validate-data":
            result = validate_data(args.manifest)
            if result["problems"]:
                print(json.dumps(result)); return 1
        elif args.command == "draft-questions":
            result = draft(args.snapshot)
        elif args.command == "validate-questions":
            questions = read_jsonl(args.questions)
            validate_questions(questions)
            result = {"questions": len(questions), "reviewed": sum(q["review_status"] == "reviewed" for q in questions)}
        elif args.command == "evaluate":
            if args.limit is not None and args.limit < 1:
                raise ValueError("Limit must be positive")
            fixture = "evaluations/native.synthetic.jsonl" if args.version.startswith("native-") else "evaluations/smoke.synthetic.jsonl"
            result = {"run_id": evaluate(f"configs/{args.version}.yaml", args.questions or (fixture if args.mode == "mock" else "evaluations/questions.jsonl"),
                       split=args.split, mode=args.mode, run_id=args.run_id, resume=args.resume, limit=args.limit)}
        elif args.command == "report":
            result = report(args.run_dir)
        elif args.command == "export-knowledge":
            meta, rows, chunks = load_snapshot(args.snapshot)
            folder = Path("data/upload") / args.snapshot
            folder.mkdir(parents=True, exist_ok=True)
            exports = []
            for row in rows:
                path = folder / f"{row['doc_id']}.md"
                header = f"doc_id: {row['doc_id']}\nsource_url: {row['source_url']}\nsnapshot: {args.snapshot}\nscope: {row['scope_notes']}\n\n"
                # Upload full frozen text; do not rebuild it from overlapping chunks or
                # remove repeated restrictions that may apply in different sections.
                source_path = Path("data/snapshots") / args.snapshot / "documents" / f"{row['doc_id']}.md"
                path.write_text(header + source_path.read_text(encoding="utf-8"), encoding="utf-8")
                exports.append({"doc_id": row["doc_id"], "upload_file": str(path.as_posix()), "sha256": digest(path.read_bytes()), "source_content_sha256": row["content_sha256"]})
            write_json(folder / "upload-manifest.json", {"snapshot": args.snapshot, "exports": exports, "uploaded": False})
            result = {"exported": len(exports), "folder": str(folder), "uploaded": False}
        elif args.command == "freeze":
            cfg_path = Path(f"configs/{args.version}.yaml")
            config = read_config(cfg_path)
            if config.get("backend") == "native":
                from .native import native_preflight
                binding = native_preflight(config)
            else:
                binding = read_config(config["binding"])
                live_preflight(config, binding)
            load_snapshot(config["knowledge_snapshot"])
            questions = read_jsonl(args.questions)
            validate_questions(questions)
            if any(q["review_status"] != "reviewed" or not q.get("reviewer") or not q.get("reviewed_at_utc") for q in questions):
                raise ValueError("Freeze requires all questions human reviewed")
            ledger_path = Path("evaluations/holdout-ledger.jsonl")
            if ledger_path.exists() and any(r["version"] == args.version for r in read_jsonl(ledger_path)):
                raise ValueError("Cannot refreeze after holdout exposure; retain original record and add independent test set")
            config["frozen"] = True
            config["freeze_reason"] = "Human confirmed fixed experiment before holdout"
            cfg_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            write_json(f"evaluations/frozen/{args.version}.json", {"experiment": experiment_state(config, binding), "questions_sha256": canonical_hash(questions), "frozen_by": args.reviewer, "at": utc_now()})
            result = {"version": args.version, "frozen": True}
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
