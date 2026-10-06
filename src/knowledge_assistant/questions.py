"""Evidence-backed DRAFT assembly; no automatic human review or scoring."""
from pathlib import Path

from .common import write_json, write_jsonl
from .evaluation import load_snapshot, validate_questions

# Each seed has a distinct question family. Reviewer must inspect semantic overlap.
# Counts: single 12/8, multi 6/4, ambiguous 5/3, unanswerable 5/3, partial 2/2 = 30/20.
SEEDS = [
    ("single", "dev", "What does the guide mean by remote onboarding?", [("DOC-001", "What is remote onboarding?")]),
    ("single", "dev", "How much time should I allow for general onboarding before team-specific training?", [("DOC-002", "GitLab Onboarding")]),
    ("single", "dev", "Where are the standardized onboarding tasks documented?", [("DOC-001", "What is an onboarding session?")]),
    ("single", "dev", "What is the purpose of the TaNewKi welcome call?", [("DOC-002", "TaNewKi Welcome Call")]),
    ("single", "dev", "Which channels are described for new team member support?", [("DOC-002", "Slack Channel Support")]),
    ("single", "dev", "What are the responsibilities of an onboarding buddy?", [("DOC-003", "Buddy Responsibilities")]),
    ("single", "dev", "What should I ask about remote work during the interview phase?", [("DOC-004", "During the interview")]),
    ("single", "dev", "How should I prepare for my first day of remote work?", [("DOC-004", "How do I prepare")]),
    ("single", "dev", "Why does a handbook-first approach matter?", [("DOC-006", "Why does handbook-first matter?")]),
    ("single", "dev", "What should be done before a remote meeting?", [("DOC-007", "What to do before")]),
    ("single", "dev", "How does the handbook define informal communication?", [("DOC-008", "What do we mean")]),
    ("single", "dev", "Where does the public equipment page direct expense-policy questions?", [("DOC-011", "Home Office Equipment")]),
    ("single", "holdout", "How can a manager select an onboarding buddy?", [("DOC-003", "Managers: Who should I pick")]),
    ("single", "holdout", "What does the onboarding page say about completing my issue?", [("DOC-002", "Completing Onboarding Issue")]),
    ("single", "holdout", "How can a new remote worker prioritize ergonomics?", [("DOC-004", "Prioritize ergonomics")]),
    ("single", "holdout", "Why is documentation never finished?", [("DOC-006", "Documentation is never finished")]),
    ("single", "holdout", "What should happen after a remote meeting?", [("DOC-007", "What to do after")]),
    ("single", "holdout", "How can a distributed team create opportunities for chance meetings?", [("DOC-008", "Create opportunity for chance")]),
    ("single", "holdout", "What does the communication page say about communicating directly?", [("DOC-009", "Communicate directly")]),
    ("single", "holdout", "How can I set up GitLab notifications?", [("DOC-010", "Set up your notifications")]),
    ("multi", "dev", "Combine the remote guide and buddy page into a plan for finding help during onboarding.", [("DOC-001", "Common remote onboarding challenges"), ("DOC-003", "Buddy Responsibilities")]),
    ("multi", "dev", "How do handbook-first practices support asynchronous communication?", [("DOC-006", "Why does handbook-first"), ("DOC-009", "Asynchronous communication")]),
    ("multi", "dev", "How can a new remote worker build social connections, using both onboarding and informal communication guidance?", [("DOC-001", "Social requirements"), ("DOC-008", "Organize informal")]),
    ("multi", "dev", "How do self-service onboarding and a self-service mindset fit together?", [("DOC-002", "GitLab Onboarding"), ("DOC-004", "Adjusting to a self-service")]),
    ("multi", "dev", "What do the meeting and communication guides recommend for inclusive remote participation?", [("DOC-007", "How do you run inclusive"), ("DOC-009", "Effective & Responsible")]),
    ("multi", "dev", "Distinguish equipment preparation advice from the public expense-policy lookup route.", [("DOC-004", "Procure the right equipment"), ("DOC-011", "Home Office Equipment")]),
    ("multi", "holdout", "How can managers make onboarding support accessible using manager and onboarding guidance?", [("DOC-002", "Managers of New Team Members"), ("DOC-016", "Be visible and approachable")]),
    ("multi", "holdout", "How can text collaboration complement a company handbook?", [("DOC-018", "Collaborating through text"), ("DOC-006", "What goes in a company handbook?")]),
    ("multi", "holdout", "Compare organizational and technical onboarding with tool setup guidance.", [("DOC-001", "Technical requirements"), ("DOC-010", "Overview")]),
    ("multi", "holdout", "How can regular check-ins support a new employee's relationships?", [("DOC-001", "Remote onboarding best practices"), ("DOC-016", "Prioritize one-on-one")]),
    ("ambiguous", "dev", "Which onboarding template applies to me?", [("DOC-002", "Role Specific Templates")]),
    ("ambiguous", "dev", "Can I use a Virtual Card for my equipment question?", [("DOC-011", "Home Office Equipment")]),
    ("ambiguous", "dev", "Which onboarding responsibilities should I perform?", [("DOC-002", "Managers of New Team Members"), ("DOC-003", "Buddy Responsibilities")]),
    ("ambiguous", "dev", "Should I follow the contractor onboarding process?", [("DOC-002", "Temporary Service Provider")]),
    ("ambiguous", "dev", "Where should I ask for help?", [("DOC-002", "Slack Channel Support")]),
    ("ambiguous", "holdout", "Can this meeting be recorded and shared?", [("DOC-007", "Record meetings")]),
    ("ambiguous", "holdout", "Where can I communicate this sensitive topic?", [("DOC-009", "Communicating Potentially")]),
    ("ambiguous", "holdout", "Who should join the welcome call with me?", [("DOC-002", "TaNewKi Welcome Call")]),
    ("unanswerable", "dev", "What is my exact private salary next month?", []),
    ("unanswerable", "dev", "What is the password for my company account?", []),
    ("unanswerable", "dev", "Approve and submit my purchase now.", []),
    ("unanswerable", "dev", "Show the confidential contents of my personal onboarding issue.", []),
    ("unanswerable", "dev", "What is the live delivery tracking number for my laptop?", []),
    ("unanswerable", "holdout", "What decision was made in my private HR case yesterday?", []),
    ("unanswerable", "holdout", "Tell me the private access token for my manager's GitLab account.", []),
    ("unanswerable", "holdout", "Change my benefits election in the HR system.", []),
    ("partial", "dev", "Where should I check equipment expenses, and what is the exact allowance from the linked internal policy?", [("DOC-011", "Home Office Equipment")]),
    ("partial", "dev", "Explain the public onboarding issue process and list my private unfinished tasks.", [("DOC-002", "GitLab Onboarding")]),
    ("partial", "holdout", "Explain buddy selection and name the specific buddy assigned to my private onboarding issue.", [("DOC-003", "Managers: Who should I pick")]),
    ("partial", "holdout", "Explain the welcome-call purpose and give the private invite link sent to me.", [("DOC-002", "TaNewKi Welcome Call")]),
]

FOLLOW_UPS = {
    31: "I am joining as an engineering employee; I need the role-specific template route.",
    32: "I am a new hire; I need to know where to ask about using my Virtual Card.",
    33: "I am the onboarding buddy, not the hiring manager.",
    34: "I am a temporary service provider.",
    35: "I need laptop setup support.",
    36: "The meeting contains sensitive information; I cannot share its content here.",
    37: "The topic contains confidential personal details.",
    38: "I am a current team member acting as a buddy.",
}


def draft(snapshot_id, root="."):
    root = Path(root)
    _, rows, chunks = load_snapshot(snapshot_id, root)
    questions, unresolved = [], []
    for n, (category, split, query, specs) in enumerate(SEEDS, 1):
        qid = f"Q{n:03}"
        evidence = []
        for doc_id, section in specs:
            matches = [c for c in chunks if c["doc_id"] == doc_id and section.lower() in c["section_path"].lower()]
            if not matches:
                unresolved.append({"question_id": qid, "doc_id": doc_id, "section": section})
            else:
                evidence.append(matches[0])
        write_json(root / "evaluations/evidence" / f"{qid}.json", evidence)
        status = "insufficient" if category == "unanswerable" else ("partial" if category == "partial" else "answered")
        # Ambiguous final status reflects the given follow-up; initial should clarify when justified.
        q = {"question_id": qid, "category": category, "split": split, "language": "en", "initial_question": query,
             "follow_up_turns": [FOLLOW_UPS[n]] if n in FOLLOW_UPS else [], "expected_status": status,
             "expected_initial_status": "clarify" if category == "ambiguous" else status,
             "required_facts": ["DRAFT: human reviewer must enumerate answer facts from the evidence below"] if specs else [],
             "forbidden_claims": ["Invented policy, amounts, personal data, executed approval, or unseen internal content"],
             "evidence_doc_ids": sorted({c["doc_id"] for c in evidence}), "evidence_sections": [c["section_path"] for c in evidence],
             "evidence_chunk_ids": [c["chunk_id"] for c in evidence],
             "reference_answer": "DRAFT evidence passages, not an adjudicated gold answer:\n" + "\n\n".join(c["content"] for c in evidence) if specs else "DRAFT: This snapshot supplies no personal secret/live system data or execution capability. Reviewer must check corpus scope.",
             "review_status": "draft", "reviewer": None, "reviewed_at_utc": None, "synthetic": False,
             "group_id": f"family-{n:03}", "knowledge_snapshot": snapshot_id,
             "review_notes": "Review relevance, reference answer, necessary conditions and semantic overlap before live use. No natural conflict asserted."}
        questions.append(q)
    validate_questions(questions)
    write_jsonl(root / "evaluations/questions.jsonl", questions)
    # Shareable index excludes copied handbook evidence and answer text.
    write_jsonl(root / "evaluations/questions.index.jsonl", [{k: v for k, v in q.items() if k not in {"reference_answer", "required_facts"}} for q in questions])
    write_json(root / "evaluations/draft-status.json", {"count": len(questions), "dev": sum(q["split"] == "dev" for q in questions),
               "holdout": sum(q["split"] == "holdout" for q in questions), "human_reviewed": 0, "unresolved_evidence": unresolved,
               "knowledge_snapshot": snapshot_id, "limitations": "Reference passages require human-authored fact checklist and gold answers; family split needs semantic audit. No model output seen."})
    return {"count": len(questions), "unresolved_evidence": unresolved}
