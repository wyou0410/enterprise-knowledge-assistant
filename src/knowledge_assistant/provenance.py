"""Resolve aggregate-file citations against the immutable source snapshot.

This proves text provenance, never semantic answer support. No model/network calls.
"""
import re
from pathlib import Path

from .common import canonical_hash, digest, write_json, write_jsonl


def comparable(text):
    # Keep words, case, numbers, negations and URLs; only remove Markdown heading
    # markers and collapse whitespace, as observed in Dify's displayed excerpts.
    return re.sub(r"\s+", " ", re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)).strip()


class SourceIndex:
    def __init__(self, snapshot, rows, texts):
        self.snapshot = snapshot
        self.rows = {r["doc_id"]: r for r in rows}
        self.texts = {k: comparable(v) for k, v in texts.items()}

    @classmethod
    def load(cls, snapshot, root="."):
        from .evaluation import load_snapshot
        _, rows, _ = load_snapshot(snapshot, root)
        folder = Path(root) / "data/snapshots" / snapshot / "documents"
        return cls(snapshot, rows, {r["doc_id"]: (folder / (r["doc_id"] + ".md")).read_text(encoding="utf-8") for r in rows})

    def resolve(self, excerpt):
        if not isinstance(excerpt, str):
            raise ValueError("Citation excerpt must be text")
        needle = comparable(excerpt)
        candidates = sorted(k for k, text in self.texts.items() if needle and needle in text)
        result = {"knowledge_snapshot": self.snapshot, "excerpt_sha256": digest(excerpt),
                  "candidate_doc_ids": candidates, "doc_id": None, "source_url": None,
                  "support_verified": False, "method": "exact_text_heading_whitespace_v1"}
        if len(needle) < 50 or len(needle.split()) < 6:
            result["resolution"] = "too_short"
        elif len(candidates) != 1:
            result["resolution"] = "ambiguous" if candidates else "unmatched"
        else:
            doc_id = candidates[0]
            result.update(resolution="unique", doc_id=doc_id, source_url=self.rows[doc_id]["source_url"],
                          source_content_sha256=self.rows[doc_id]["content_sha256"])
        return result


def build(snapshot, root="."):
    from .evaluation import load_snapshot
    root = Path(root)
    _, rows, chunks = load_snapshot(snapshot, root)
    folder = root / "data/provenance" / snapshot
    index = SourceIndex.load(snapshot, root)
    sources = [{k: r[k] for k in ["doc_id", "title", "source_url", "content_sha256", "raw_sha256", "scope_notes"]} for r in rows]
    write_json(folder / "sources.json", {"snapshot": snapshot, "documents": sources,
               "sources_sha256": canonical_hash(sources), "dify_document_uuid": "a09ec6e0-6285-4434-93b2-c381a5d325f7",
               "binding_kind": "aggregate_one_to_many_not_a_verified_segment_map"})
    # Shareable metadata only; no handbook bodies or human reference answers.
    write_json(root / "reports/source-map.json", {"snapshot": snapshot, "documents": sources,
               "sources_sha256": canonical_hash(sources),
               "aggregate_document_uuid": "a09ec6e0-6285-4434-93b2-c381a5d325f7",
               "remote_segments_verified": False})
    audit = [{"local_chunk_id": c["chunk_id"], "expected_doc_id": c["doc_id"], **index.resolve(c["content"])} for c in chunks]
    write_jsonl(folder / "local-chunk-audit.jsonl", audit)
    counts = {s: sum(r["resolution"] == s for r in audit) for s in ["unique", "ambiguous", "unmatched", "too_short"]}
    wrong = [r["local_chunk_id"] for r in audit if r["resolution"] == "unique" and r["doc_id"] != r["expected_doc_id"]]
    report = {"snapshot": snapshot, "source_documents": len(sources), "local_chunks": len(audit),
              "counts": counts, "wrong_unique_matches": wrong, "dify_api_verified": False,
              "note": "Local chunk provenance audit, not Dify retrieval accuracy or full remote segment mapping."}
    write_json(root / "reports/provenance-audit.json", report)
    return report
