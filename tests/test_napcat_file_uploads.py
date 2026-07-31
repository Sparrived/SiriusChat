from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest

from sirius_pulse.platforms.onebot_v11.napcat.adapter import NapCatAdapter


class _DownloadResponse:
    def __init__(self, chunks: list[bytes]):
        self._chunks = iter(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int = -1) -> bytes:
        return next(self._chunks, b"")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "target_id", "action_name", "target_key"),
    [
        ("upload_group_file", 9001, "upload_group_file", "group_id"),
        ("upload_private_file", 10001, "upload_private_file", "user_id"),
    ],
)
async def test_napcat_upload_stages_local_file_for_the_other_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    target_id: int,
    action_name: str,
    target_key: str,
):
    source = tmp_path / "source" / "container report.txt"
    source.parent.mkdir()
    source.write_bytes(b"container report")
    shared_root = tmp_path / "shared-upload"
    monkeypatch.setenv("SIRIUS_NAPCAT_UPLOAD_ROOT", str(shared_root))
    monkeypatch.setenv("SIRIUS_NAPCAT_UPLOAD_TARGET_ROOT", "/sirius-upload")

    adapter = NapCatAdapter("ws://example.invalid")
    observed: dict[str, object] = {}

    async def fake_call_api(action: str, params: dict[str, object]) -> dict[str, object]:
        staged_files = list(shared_root.iterdir())
        assert len(staged_files) == 1
        assert staged_files[0].read_bytes() == b"container report"
        assert params == {
            target_key: target_id,
            "file": f"file:///sirius-upload/{quote(staged_files[0].name)}",
            "name": "report.txt",
        }
        observed["action"] = action
        return {"data": {"file_id": "file-1"}}

    monkeypatch.setattr(adapter, "call_api", fake_call_api)
    result = await getattr(adapter, method_name)(target_id, str(source), "report.txt")

    assert result == {"data": {"file_id": "file-1"}}
    assert observed["action"] == action_name
    assert list(shared_root.iterdir()) == []


@pytest.mark.asyncio
async def test_napcat_upload_preserves_remote_file_reference(monkeypatch: pytest.MonkeyPatch):
    adapter = NapCatAdapter("ws://example.invalid")
    captured: dict[str, object] = {}

    async def fake_call_api(action: str, params: dict[str, object]) -> dict[str, object]:
        captured["action"] = action
        captured["params"] = params
        return {"data": {"file_id": "file-1"}}

    monkeypatch.setattr(adapter, "call_api", fake_call_api)
    await adapter.upload_group_file(9001, "https://example.test/report.txt", "report.txt")

    assert captured == {
        "action": "upload_group_file",
        "params": {
            "group_id": 9001,
            "file": "https://example.test/report.txt",
            "name": "report.txt",
        },
    }


@pytest.mark.asyncio
async def test_napcat_upload_removes_partial_copy_when_staging_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "source.txt"
    source.write_text("report", encoding="utf-8")
    shared_root = tmp_path / "shared-upload"
    monkeypatch.setenv("SIRIUS_NAPCAT_UPLOAD_ROOT", str(shared_root))

    def partial_copy(_: Path, destination: Path) -> None:
        destination.write_text("partial", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(
        "sirius_pulse.platforms.onebot_v11.napcat.adapter.shutil.copyfile", partial_copy
    )
    adapter = NapCatAdapter("ws://example.invalid")

    with pytest.raises(OSError, match="disk full"):
        await adapter.upload_group_file(9001, str(source), "report.txt")

    assert list(shared_root.iterdir()) == []


@pytest.mark.asyncio
async def test_napcat_group_file_list_uses_root_and_folder_actions(
    monkeypatch: pytest.MonkeyPatch,
):
    adapter = NapCatAdapter("ws://example.invalid")
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_call_api(action: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((action, params))
        return {"data": {"files": [], "folders": []}}

    monkeypatch.setattr(adapter, "call_api", fake_call_api)

    await adapter.get_group_file_list(9001)
    await adapter.get_group_file_list(9001, "folder-1", 20)

    assert calls == [
        ("get_group_root_files", {"group_id": 9001, "file_count": 50}),
        (
            "get_group_files_by_folder",
            {"group_id": 9001, "file_count": 20, "folder_id": "folder-1"},
        ),
    ]


@pytest.mark.asyncio
async def test_napcat_downloads_group_file_url_to_persona_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    adapter = NapCatAdapter("ws://example.invalid", work_path=tmp_path)
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_call_api(action: str, params: dict[str, object]) -> dict[str, object]:
        calls.append((action, params))
        return {"data": {"url": "https://example.test/file.bin"}}

    monkeypatch.setattr(adapter, "call_api", fake_call_api)
    monkeypatch.setattr(
        "sirius_pulse.platforms.onebot_v11.napcat.adapter.urlopen",
        lambda *_args, **_kwargs: _DownloadResponse([b"file", b"-bytes"]),
    )

    result = await adapter.download_group_file(9001, "file-1", "../report.bin")

    output = Path(result["path"])
    assert output == (tmp_path / "group_files" / "report.bin").resolve()
    assert output.read_bytes() == b"file-bytes"
    assert result["size"] == 10
    assert calls == [
        ("get_group_file_url", {"group_id": 9001, "file_id": "file-1"}),
    ]
