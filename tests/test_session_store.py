from pathlib import Path

from sirius_chat.models import Message, TokenUsageRecord, Transcript
from sirius_chat.session_store import SqliteSessionStore


def test_sqlite_session_store_save_and_load(tmp_path: Path) -> None:
    store = SqliteSessionStore(tmp_path)
    transcript = Transcript(
        messages=[
            Message(
                role="user",
                speaker="A",
                content="hello",
                multimodal_inputs=[{"type": "image", "value": "https://example.com/a.png"}],
            )
        ]
    )
    transcript.orchestration_stats = {
        "multimodal_parse": {"attempted": 1, "succeeded": 1},
    }
    transcript.add_token_usage_record(
        TokenUsageRecord(
            actor_id="assistant",
            task_name="chat_main",
            model="main-model",
            prompt_tokens=40,
            completion_tokens=20,
            total_tokens=60,
            retries_used=1,
        )
    )

    store.save(transcript)

    assert store.exists()
    loaded = store.load()
    assert loaded.messages[-1].content == "hello"
    assert loaded.messages[-1].speaker == "A"
    assert loaded.messages[-1].multimodal_inputs == [{"type": "image", "value": "https://example.com/a.png"}]
    assert loaded.orchestration_stats["multimodal_parse"]["succeeded"] == 1
    assert len(loaded.token_usage_records) == 1
    assert loaded.token_usage_records[0].total_tokens == 60
    assert loaded.token_usage_records[0].retries_used == 1


