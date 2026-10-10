"""Real media delivery, degradation and shared temporary-file lifetime."""

import asyncio
import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_media import real_video
from tests.test_media_api import login
from tests.test_mvp_flow import wait_terminal
from tests.test_video import corrupt_second_audio_track, make_video


@pytest.mark.parametrize("input_mode", ["upload", "share"])
def test_real_video_download_is_private_mp4_and_supports_ranges(real_tools, tmp_path, input_mode):
    from app.resolver.base import ResolvedVideo

    source = real_video(tmp_path, real_tools)
    original = source.read_bytes()
    app = create_app(real_tools)
    downloads = []

    class SampleResolver:
        async def resolve(self, url):
            return ResolvedVideo("https://example.com/source", title="完整画面与声音")

    async def download(url, target, max_bytes):
        downloads.append(url)
        shutil.copyfile(source, target)

    # Replace only the external platform/network boundaries with a real local movie.
    app.state.tasks.resolvers["douyin"] = SampleResolver()
    app.state.tasks.network.download = download
    with TestClient(app) as client:
        token = login(client, real_tools)
        if input_mode == "upload":
            response = client.post(
                "/api/tasks/upload?filename=完整画面与声音.mp4",
                content=original,
                headers={"X-CSRF-Token": token},
            )
        else:
            response = client.post(
                "/api/tasks/link",
                json={"share_text": "https://v.douyin.com/example/"},
                headers={"X-CSRF-Token": token},
            )
        assert response.status_code == 202
        task = wait_terminal(client, response.json()["id"])
        assert task["state"] == "ready", task["message"]
        assert task.get("video_download_url"), "缺少原视频下载入口"
        assert task["video_filename"] == "完整画面与声音.mp4"
        assert task["video_message"] == ""
        full = client.get(task["video_download_url"])
        assert full.status_code == 200
        assert full.content == original
        assert full.headers["content-type"] == "video/mp4"
        assert "attachment" in full.headers["content-disposition"]
        assert "filename*=utf-8''" in full.headers["content-disposition"]
        assert ".mp4" in full.headers["content-disposition"]
        assert client.head(task["video_download_url"]).headers["content-length"] == str(
            len(original)
        )
        ranged = client.get(task["video_download_url"], headers={"Range": "bytes=1-31"})
        assert ranged.status_code == 206
        assert ranged.content == original[1:32]
        assert (
            client.get(task["video_download_url"], headers={"Range": "bytes=invalid"}).status_code
            == 400
        )
        assert (
            client.get(
                task["video_download_url"], headers={"Range": f"bytes={len(original)}-"}
            ).status_code
            == 416
        )
        assert (
            client.get(f"/files/{task['id']}?kind=video")
            .headers["content-disposition"]
            .startswith("attachment")
        )
        assert client.get(f"/files/{task['id']}?kind=input").status_code == 422
        assert client.get(task["download_url"]).headers["content-type"] == "audio/mpeg"
        internal = app.state.tasks.tasks[task["id"]]
        assert not (internal.directory / "input").exists()
        assert (internal.directory / "video.mp4").stat().st_mode & 0o777 == 0o600
        assert internal.deliveries == 0
        assert len(downloads) == (1 if input_mode == "share" else 0)
        client.cookies.clear()
        assert client.get(task["video_download_url"]).status_code == 401
        login(client, real_tools)
        assert client.get(task["video_download_url"]).status_code == 404
    assert not internal.directory.exists()


def test_two_deliveries_protect_both_files_until_the_last_response(settings):
    from app.services.errors import MediaError
    from app.services.media import MediaProcessor
    from app.services.network import SafeHTTP
    from app.services.tasks import TaskManager

    async def exercise():
        manager = TaskManager(settings, MediaProcessor(settings), {}, SafeHTTP())
        await manager.open()
        try:
            task = manager.reserve("owner", "sample", require_media=False)
            (task.directory / "audio.mp3").write_bytes(b"audio")
            (task.directory / "video.mp4").write_bytes(b"video")
            task.video_ready = True
            manager._finish(task, "ready", "done")
            manager.acquire_file(task)
            manager.acquire_file(task, "video")
            task.expires_at = time.time() - 1
            with pytest.raises(MediaError):
                manager.get(task.id, "owner")
            await manager.release_file(task)
            assert (task.directory / "audio.mp3").exists()
            assert (task.directory / "video.mp4").exists()
            await manager.release_file(task)
            assert not task.directory.exists()
        finally:
            await manager.close()

    asyncio.run(exercise())


def test_unsupported_video_keeps_successful_mp3(real_tools, tmp_path):
    original = real_video(tmp_path, real_tools)
    unsupported = tmp_path / "source.avi"
    subprocess.run(
        [
            shutil.which(real_tools.ffmpeg_bin),
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(original),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "wmv2",
            "-c:a",
            "pcm_s16le",
            str(unsupported),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    app = create_app(real_tools)
    with TestClient(app) as client:
        token = login(client, real_tools)
        response = client.post(
            "/api/tasks/upload?filename=旧视频.avi",
            content=unsupported.read_bytes(),
            headers={"X-CSRF-Token": token},
        )
        task = wait_terminal(client, response.json()["id"])
        assert task["state"] == "ready", task["message"]
        assert task["video_download_url"] is None
        assert task["video_filename"] is None
        assert task["video_message"] == "原视频暂无法保存为 MP4，MP3 仍可下载"
        assert client.get(task["download_url"]).content
        unavailable = client.get(f"/files/{task['id']}?kind=video")
        assert unavailable.status_code == 409
        assert unavailable.json()["code"] == "video_unavailable"
        directory = app.state.tasks.tasks[task["id"]].directory
        assert sorted(path.name for path in directory.iterdir()) == ["audio.mp3"]


def test_failed_video_cleanup_retries_without_removing_live_mp3(
    real_tools, tmp_path, monkeypatch, caplog
):
    import logging
    from pathlib import Path

    from app.services.errors import MediaError
    from app.services.media import MediaProcessor
    from app.services.network import SafeHTTP
    from app.services.tasks import TaskManager
    from tests.test_video import write_wrapper

    source = make_video(tmp_path, real_tools, container="mov")
    executable = shutil.which(real_tools.ffmpeg_bin)
    wrapper = write_wrapper(
        tmp_path / "failed-remux",
        f"""
import os, pathlib, sys
if sys.argv[-1].endswith('.mp4'):
    pathlib.Path(sys.argv[-1]).write_bytes(b'partial-video')
    sys.exit(1)
os.execv({executable!r}, [{executable!r}, *sys.argv[1:]])
""",
    )
    configured = real_tools.model_copy(update={"ffmpeg_bin": wrapper})
    original_unlink = Path.unlink
    unavailable = True

    def unlink(path, *args, **kwargs):
        if path.name == "video.mp4" and unavailable:
            raise PermissionError("private transient storage failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    async def exercise():
        nonlocal unavailable
        manager = TaskManager(configured, MediaProcessor(configured), {}, SafeHTTP())
        await manager.open()
        try:
            task = manager.reserve("owner", "sample")
            shutil.copyfile(source, task.directory / "input")
            manager.start(task)
            await task.runner
            assert task.state == "ready", task.message
            assert manager.view(task)["video_download_url"] is None
            with pytest.raises(MediaError) as exc:
                manager.acquire_file(task, "video")
            assert exc.value.code == "video_unavailable"
            audio = manager.acquire_file(task)
            original_audio = audio.read_bytes()
            assert original_audio
            assert (task.directory / "video.mp4").read_bytes() == b"partial-video"
            await manager.sweep()
            await manager.sweep()
            unavailable = False
            await manager.sweep()
            assert sorted(path.name for path in task.directory.iterdir()) == ["audio.mp3"]
            assert audio.read_bytes() == original_audio
            assert manager.get(task.id, "owner").state == "ready"
            task.expires_at = time.time() - 1
            await manager.sweep()
            assert audio.exists()
            await manager.release_file(task)
            assert not task.directory.exists()
        finally:
            unavailable = False
            await manager.close()

    with caplog.at_level(logging.INFO, logger="app.services.tasks"):
        asyncio.run(exercise())
    assert sum(record.msg == "task.cleanup_deferred" for record in caplog.records) == 1
    assert sum(record.msg == "task.cleaned" for record in caplog.records) == 1
    assert "private transient storage failure" not in caplog.text


def test_corrupt_default_second_audio_track_upload_keeps_only_mp3(real_tools, tmp_path):
    source = make_video(tmp_path, real_tools)
    corrupt_second_audio_track(real_tools, source)
    app = create_app(real_tools)
    with TestClient(app) as client:
        token = login(client, real_tools)
        response = client.post(
            "/api/tasks/upload?filename=双音轨损坏.mp4",
            content=source.read_bytes(),
            headers={"X-CSRF-Token": token},
        )
        assert response.status_code == 202
        task = wait_terminal(client, response.json()["id"])
        assert task["state"] == "ready", task["message"]
        assert task["video_download_url"] is None
        assert task["video_filename"] is None
        assert task["video_message"] == "原视频暂无法保存为 MP4，MP3 仍可下载"
        audio = client.get(task["download_url"])
        assert audio.status_code == 200
        assert audio.content
        unavailable = client.get(f"/files/{task['id']}?kind=video")
        assert unavailable.status_code == 409
        assert unavailable.json()["code"] == "video_unavailable"
        directory = app.state.tasks.tasks[task["id"]].directory
        assert sorted(path.name for path in directory.iterdir()) == ["audio.mp3"]


@pytest.mark.parametrize("mode", ["cancel", "task_timeout", "media_timeout"])
def test_video_stage_cancel_or_task_timeout_removes_both_outputs(real_tools, tmp_path, mode):
    import os
    import sys

    movie = real_video(tmp_path, real_tools)
    source = tmp_path / "source.mov"
    executable = shutil.which(real_tools.ffmpeg_bin)
    subprocess.run(
        [executable, "-v", "error", "-i", str(movie), "-map", "0", "-c", "copy", str(source)],
        check=True,
        capture_output=True,
        timeout=15,
    )
    marker = tmp_path / "video-stage-pid"
    wrapper = tmp_path / "slow-video-ffmpeg"
    wrapper.write_text(f"""#!{sys.executable}
import os, pathlib, sys
args = sys.argv[1:]
if args[-1].endswith('.mp4'):
    pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
    args.insert(args.index('-i'), '-re')
os.execv({executable!r}, [{executable!r}, *args])
""")
    wrapper.chmod(0o700)
    configured = real_tools.model_copy(
        update={
            "ffmpeg_bin": str(wrapper),
            "task_timeout_seconds": 1 if mode == "task_timeout" else 30,
        }
    )
    app = create_app(configured)
    if mode == "media_timeout":
        # A shorter processor deadline must fail the task rather than degrade.
        app.state.tasks.media.settings = configured.model_copy(update={"task_timeout_seconds": 1})
    with TestClient(app) as client:
        token = login(client, configured)
        response = client.post(
            "/api/tasks/upload?filename=真实慢速.mov",
            content=source.read_bytes(),
            headers={"X-CSRF-Token": token},
        )
        task_id = response.json()["id"]
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), "未进入真实 MP4 封装阶段"
        internal = app.state.tasks.tasks[task_id]
        assert (internal.directory / "audio.mp3").exists()
        if mode == "cancel":
            result = client.post(f"/api/tasks/{task_id}/cancel", headers={"X-CSRF-Token": token})
            assert result.status_code == 200
        task = wait_terminal(client, task_id)
        assert task["state"] == ("cancelled" if mode == "cancel" else "failed")
        assert task["download_url"] is None
        assert task["video_download_url"] is None
        assert not internal.directory.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
