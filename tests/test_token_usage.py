from sirius_chat.models import TokenUsageRecord, Transcript
from sirius_chat.api import build_token_usage_baseline, summarize_token_usage


def test_token_usage_baseline_and_summary() -> None:
    transcript = Transcript()
    transcript.add_token_usage_record(
        TokenUsageRecord(
            actor_id="u1",
            task_name="memory_extract",
            model="memory-model",
            prompt_tokens=20,
            completion_tokens=10,
            total_tokens=30,
            retries_used=1,
        )
    )
    transcript.add_token_usage_record(
        TokenUsageRecord(
            actor_id="assistant",
            task_name="chat_main",
            model="main-model",
            prompt_tokens=30,
            completion_tokens=20,
            total_tokens=50,
            retries_used=0,
        )
    )

    baseline = build_token_usage_baseline(transcript.token_usage_records)
    assert baseline.total_calls == 2
    assert baseline.total_prompt_tokens == 50
    assert baseline.total_completion_tokens == 30
    assert baseline.total_tokens == 80
    assert baseline.retry_rate == 0.5

    summary = summarize_token_usage(transcript)
    assert summary["baseline"]["total_calls"] == 2
    assert summary["by_actor"]["u1"]["total_tokens"] == 30
    assert summary["by_actor"]["assistant"]["total_tokens"] == 50
    assert summary["by_task"]["memory_extract"]["calls"] == 1
    assert summary["by_task"]["chat_main"]["calls"] == 1
    assert summary["by_model"]["memory-model"]["total_tokens"] == 30
    assert summary["by_model"]["main-model"]["total_tokens"] == 50



