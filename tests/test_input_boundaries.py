import pytest
import yaml

from knowledge_assistant.common import read_jsonl
from knowledge_assistant.data import fetch
from knowledge_assistant.evaluation import load_snapshot, validate_questions


@pytest.mark.parametrize("identifier", ["../escape", "/absolute", "C:\\escape", "", ["not-a-string"]])
def test_question_id_cannot_be_an_output_path(identifier):
    question = read_jsonl("evaluations/native.synthetic.jsonl")[0]
    question["question_id"] = identifier
    with pytest.raises(ValueError, match="safe ASCII"):
        validate_questions([question], real=False)


def test_source_id_checked_before_network(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"sources": [{"doc_id": "../escape", "url": "https://handbook.gitlab.com/test/"}]}))
    class NoNetwork:
        def get(self, *args, **kwargs):
            pytest.fail("Unsafe source ID reached network")
    with pytest.raises(ValueError, match="safe ASCII"):
        fetch(path, root=tmp_path, session=NoNetwork())


@pytest.mark.parametrize("snapshot", [None, ""])
def test_unconfigured_snapshot_has_actionable_error(snapshot, tmp_path):
    with pytest.raises(ValueError, match="knowledge_snapshot"):
        load_snapshot(snapshot, tmp_path)
