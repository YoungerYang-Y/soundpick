"""Runtime readiness uses actual processes without requiring installed media tools."""

import asyncio
import os
import sys

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.media import MediaProcessor
from tests.test_media_api import login


def readiness_tools(settings, tmp_path, *, encoder="libmp3lame"):
    for name in ("ffmpeg", "ffprobe"):
        executable = tmp_path / name
        executable.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            f"if '-version' in sys.argv: print('{name} version test')\n"
            f"elif '-encoders' in sys.argv: print(' A....D {encoder} Test encoder')\n"
            "else: sys.exit(1)\n"
        )
        executable.chmod(0o700)
    return settings.model_copy(
        update={"ffmpeg_bin": str(tmp_path / "ffmpeg"), "ffprobe_bin": str(tmp_path / "ffprobe")}
    )


@pytest.mark.parametrize("failure", ["broken_executable", "missing_encoder"])
def test_startup_disables_bad_media_tools_and_rejects_tasks(settings, tmp_path, failure):
    configured = readiness_tools(settings, tmp_path, encoder="aac")
    if failure == "broken_executable":
        configured = configured.model_copy(update={"ffmpeg_bin": "/bin/false"})
    app = create_app(configured)
    with TestClient(app) as client:
        token = login(client, configured)
        assert client.get("/healthz").status_code == 200
        capabilities = client.get("/api/capabilities").json()
        assert not capabilities["media_available"]
        assert not capabilities["extraction_available"]
        response = client.post(
            "/api/tasks/reservations",
            json={"task_id": "a" * 32, "filename": "video.mp4"},
            headers={"X-CSRF-Token": token},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "media_unavailable"
        assert not app.state.tasks.tasks


def test_readiness_accepts_runnable_tools_and_exact_mp3_encoder(settings, tmp_path):
    media = MediaProcessor(readiness_tools(settings, tmp_path))
    assert asyncio.run(media.check_readiness())
    assert media.available
    assert media.versions == {"FFmpeg": "ffmpeg version test", "FFprobe": "ffprobe version test"}
    assert media.readiness_error is None


@pytest.mark.parametrize("failure", ["oversized_output", "timeout"])
def test_readiness_bounds_probe_output_and_reaps_process(settings, tmp_path, monkeypatch, failure):
    configured = readiness_tools(settings, tmp_path)
    marker = tmp_path / "pid"
    executable = tmp_path / "ffmpeg"
    behavior = "print('x' * 65537)" if failure == "oversized_output" else "time.sleep(30)"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, time\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        f"{behavior}\n"
    )
    monkeypatch.setattr("app.services.media._READINESS_TIMEOUT", 0.2)
    media = MediaProcessor(configured)
    assert not asyncio.run(media.check_readiness())
    assert not media.available
    assert media.readiness_error
    assert str(tmp_path) not in media.readiness_error
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
