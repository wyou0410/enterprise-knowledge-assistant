"""Scripted protocol demo ONLY. No retrieval or model generation."""
import json

SYNTHETIC_DOCS = [
    {"doc_id": "SYN-001", "source_url": "https://example.invalid/fictional/onboarding", "included": "true", "synthetic": "true"},
    {"doc_id": "SYN-002", "source_url": "https://example.invalid/fictional/equipment", "included": "true", "synthetic": "true"},
    {"doc_id": "SYN-003", "source_url": "https://example.invalid/fictional/equipment-old", "included": "true", "synthetic": "true"},
]
SYNTHETIC_BINDING = {"dataset_id": "synthetic", "document_map": {"fiction-1": "SYN-001", "fiction-2": "SYN-002", "fiction-3": "SYN-003"}}


def response(case, turn):
    status = "answered"
    citations = []
    clarification = None
    answer = "MOCK / Fictional Example Co only: Read the welcome checklist and contact your buddy."
    if case == "insufficient":
        status, answer = "insufficient", "MOCK: The fictional snapshot has no salary policy. This does not imply no policy exists."
    elif case == "clarify" and turn == 0:
        status, clarification = "clarify", "MOCK: Is your fictional role employee or contractor?"
        answer = "MOCK: The fixture has different equipment routes for employees and contractors."
    elif case == "clarify":
        answer = "MOCK: For the original equipment request, the fictional employee route is to contact the equipment desk before ordering."
    elif case == "partial":
        status, answer = "partial", "MOCK: The fixture explains buddy support but has no relocation allowance details."
    elif case == "conflict":
        status, answer = "conflict", "MOCK: Two fictional equipment documents disagree about the approver; ask the policy owner to resolve this."
    elif case == "new_topic" and turn:
        status, answer = "insufficient", "MOCK: New salary topic; old equipment conditions are not used. No evidence in this snapshot."
    elif case == "injection":
        answer = "MOCK: Ignore-rule text is untrusted document data. No instruction or tool was executed."
    if status != "insufficient":
        ids = ["fiction-2", "fiction-3"] if case == "conflict" else (["fiction-2"] if case == "clarify" else ["fiction-1"])
        for i, doc_id in enumerate(ids):
            citations.append({"dataset_id": "synthetic", "document_id": doc_id, "segment_id": "fixture-segment-"+str(i),
                              "position": i+1, "content": "Synthetic evidence; see tests/fixtures/company.md"})
    return {"answer": json.dumps({"status": status, "answer": answer, "clarification_question": clarification,
            "citation_doc_ids": [SYNTHETIC_BINDING["document_map"][c["document_id"]] for c in citations]}),
            "conversation_id": "scripted-conversation", "message_id": f"scripted-{case}-{turn}",
            "metadata": {"retriever_resources": citations}}
