"""Dify blocking Chatflow adapter. No provider API keys are needed here."""
import json
import os
import re
import time
from urllib.parse import urlparse

import requests

STATUSES = {"answered", "partial", "clarify", "insufficient", "conflict", "error"}


class DifyClient:
    def __init__(self, base_url=None, api_key=None, session=None, timeout=60, retries=2, sleep=time.sleep):
        self.base_url = (base_url or os.environ.get("DIFY_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("DIFY_APP_API_KEY", "")
        self.session = session or requests.Session()
        self.timeout, self.retries, self.sleep = timeout, retries, sleep

    def chat(self, question, user, conversation_id=None, workflow_id=None, inputs=None):
        start = time.perf_counter()
        if not self.api_key or not self.base_url:
            return None, {"code": "missing_credentials", "retryable": False}, None, 0
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}):
            return None, {"code": "insecure_base_url", "retryable": False}, None, 0
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            return None, {"code": "invalid_base_url", "retryable": False}, None, 0
        body = {"query": question, "user": user, "inputs": inputs or {}, "response_mode": "blocking",
                "auto_generate_name": False}
        if conversation_id:
            body["conversation_id"] = conversation_id
        if workflow_id:
            body["workflow_id"] = workflow_id
        for attempt in range(self.retries + 1):
            try:
                response = self.session.post(self.base_url + "/chat-messages", json=body,
                                             headers={"Authorization": "Bearer " + self.api_key},
                                             timeout=self.timeout, allow_redirects=False)
                code = response.status_code
                if code == 429:
                    if attempt < self.retries:
                        delay = response.headers.get("Retry-After", "1")
                        self.sleep(min(float(delay) if str(delay).isdigit() else 1, 10))
                        continue
                    error = {"code": "http_429", "retryable": True}
                elif code >= 400 or 300 <= code < 400:
                    error = {"code": f"http_{code}", "retryable": code >= 500}
                else:
                    try:
                        data = response.json()
                    except ValueError:
                        data = None
                    if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
                        error = {"code": "invalid_json_or_schema", "retryable": False}
                    else:
                        return data, None, round((time.perf_counter()-start)*1000, 3), attempt + 1
            except requests.Timeout:
                # Ambiguous POST completion: no automatic retry to avoid duplicate cost/turns.
                error = {"code": "timeout_completion_unknown", "retryable": False}
            except requests.RequestException:
                error = {"code": "network_completion_unknown", "retryable": False}
            return None, error, round((time.perf_counter()-start)*1000, 3), attempt + 1


def normalize(data, request_id, version, snapshot, manifest, binding, latency_ms=None, error=None, mode="live", source_index=None):
    result = {"request_id": request_id, "status": None, "answer": "", "clarification_question": None,
              "citations": [], "retrieved_chunks": None, "workflow_version": version,
              "knowledge_snapshot": snapshot, "latency_ms": latency_ms, "usage": None, "error": error,
              "mode": mode, "conversation_id": None, "message_id": None, "field_notes": {
                  "retrieved_chunks": "Blocking retriever_resources are citation resources, not a verified full Top-K trace.",
                  "status": "Null unless a validated structured status is actually returned."}}
    if error:
        result["status"] = "error"
        return result
    try:
        if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
            raise ValueError("invalid_response")
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("invalid_metadata")
        resources = metadata.get("retriever_resources")
        if resources is not None and not isinstance(resources, list):
            raise ValueError("invalid_resources")
        docs = {r["doc_id"]: r for r in manifest if r["included"] == "true"}
        mapping = binding.get("document_map", {})
        aggregate_ids = binding.get("aggregate_document_ids", [])
        if not isinstance(aggregate_ids, list) or any(not isinstance(x, str) or not x for x in aggregate_ids):
            raise ValueError("invalid_aggregate_binding")
        if aggregate_ids and (source_index is None or source_index.snapshot != snapshot or set(source_index.rows) != set(docs)):
            raise ValueError("aggregate_snapshot_not_verified")
        valid_urls = {r["source_url"] for r in docs.values()}
        for resource in resources or []:
            if not isinstance(resource, dict):
                raise ValueError("invalid_resource")
            dify_id = resource.get("document_id")
            doc_id = mapping.get(dify_id)
            if binding.get("dataset_id") and resource.get("dataset_id") != binding["dataset_id"]:
                raise ValueError("unknown_citation_document")
            excerpt = resource.get("content")
            provenance = None
            if dify_id in aggregate_ids:
                provenance = source_index.resolve(excerpt)
                if provenance["resolution"] != "unique":
                    result.setdefault("unresolved_citations", []).append({"document_id": dify_id,
                        "chunk_id": resource.get("segment_id"), "excerpt": excerpt, **provenance})
                    continue
                doc_id = provenance["doc_id"]
            if doc_id not in docs:
                raise ValueError("unknown_citation_document")
            result["citations"].append({"doc_id": doc_id, "chunk_id": resource.get("segment_id"),
                "source_url": docs[doc_id]["source_url"], "excerpt": excerpt, "position": resource.get("position"),
                "score": resource.get("score"), "support_verified": False})
            if provenance:
                result["citations"][-1]["provenance"] = provenance
        if result.get("unresolved_citations"):
            result["field_notes"]["unresolved_citations"] = "Short, duplicate or unmatched aggregate excerpts have no verified per-source attribution; no source invented."
        answer = data["answer"]
        try:
            structured = json.loads(answer)
        except ValueError:
            structured = None
        if isinstance(structured, dict):
            if structured.get("status") not in STATUSES or not isinstance(structured.get("answer"), str):
                raise ValueError("invalid_answer_contract")
            cited_ids = structured.get("citation_doc_ids", [])
            if not isinstance(cited_ids, list) or any(not isinstance(x, str) for x in cited_ids):
                raise ValueError("invalid_citation_ids")
            available = {c["doc_id"] for c in result["citations"]}
            if not set(cited_ids) <= available:
                raise ValueError("citation_not_in_resources")
            clarification = structured.get("clarification_question")
            if clarification is not None and not isinstance(clarification, str):
                raise ValueError("invalid_clarification")
            if structured["status"] == "clarify" and not clarification:
                raise ValueError("missing_clarification")
            if structured["status"] in {"answered", "partial", "conflict", "clarify"} and not result["citations"]:
                raise ValueError("missing_evidence_resources")
            if structured["status"] == "conflict" and len({c["doc_id"] for c in result["citations"]}) < 2:
                raise ValueError("conflict_needs_multiple_sources")
            result.update(status=structured["status"], answer=structured["answer"], clarification_question=clarification)
            result["field_notes"].pop("status")
        else:
            result["answer"] = answer
        for url in re.findall(r"https?://[^\s<>\)\]\"']+", result["answer"] + " " + (result["clarification_question"] or "")):
            if url.rstrip(".,;") not in valid_urls:
                raise ValueError("unapproved_answer_url")
        result.update(conversation_id=data.get("conversation_id"), message_id=data.get("message_id"),
                      usage=metadata.get("usage"))
        if not resources:
            result["field_notes"]["citations"] = "No citation resources exposed; not evidence of no retrieval."
    except (ValueError, TypeError, KeyError) as exc:
        result.update(status="error", answer="", citations=[], error={"code": str(exc) if isinstance(exc, ValueError) else "invalid_response_schema", "retryable": False})
    return result
