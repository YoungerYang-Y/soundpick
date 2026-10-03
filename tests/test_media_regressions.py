"""Regressions for valid audio paired with unusable video and cancellation races."""

import asyncio
import shutil
import subprocess

import pytest

from app.services.errors import MediaError
from app.services.media import MediaProcessor
from tests.test_video import make_video, probe


def test_damaged_video_packets_keep_audio_but_reject_mp4(real_tools, tmp_path):
    source = make_video(tmp_path, real_tools)
    info = probe(real_tools, source, packets=True)
    video_indices = {s["index"] for s in info["streams"] if s["codec_type"] == "video"}
    damaged = bytearray(source.read_bytes())
    for packet in info["packets"]:
        if packet["stream_index"] in video_indices:
            start, size = int(packet["pos"]), int(packet["size"])
            damaged[start : start + size] = b"\x00" * size
    source.write_bytes(damaged)
    processor = MediaProcessor(real_tools)
    audio = tmp_path / "audio.mp3"
    assert abs(asyncio.run(processor.extract_audio(source, audio)) - 3) <= 0.25
    video = tmp_path / "video.mp4"
    with pytest.raises(MediaError) as error:
        asyncio.run(processor.prepare_video(source, video))
    assert error.value.code == "conversion_failed"
    assert source.read_bytes() == damaged
    assert audio.exists()
    assert not video.exists()


@pytest.mark.parametrize("method,suffix", [("extract_audio", "mp3"), ("prepare_video", "mp4")])
def test_attached_picture_is_not_video(real_tools, tmp_path, method, suffix):
    ffmpeg = shutil.which(real_tools.ffmpeg_bin)
    cover = tmp_path / "cover.jpg"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=160x120",
            "-frames:v",
            "1",
            str(cover),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    source = tmp_path / "cover-audio.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=3",
            "-i",
            str(cover),
            "-map",
            "0:a",
            "-map",
            "1:v",
            "-c:a",
            "aac",
            "-c:v",
            "copy",
            "-disposition:v",
            "attached_pic",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    streams = probe(real_tools, source)["streams"]
    assert [s["disposition"]["attached_pic"] for s in streams if s["codec_type"] == "video"] == [1]
    processor = MediaProcessor(real_tools)
    target = tmp_path / f"output.{suffix}"
    with pytest.raises(MediaError) as error:
        asyncio.run(getattr(processor, method)(source, target))
    assert error.value.code == "missing_video"
    assert source.exists()
    assert not target.exists()


def test_full_mp4_decode_is_bounded_and_reaps_on_timeout(real_tools, tmp_path):
    import os

    from tests.test_video import write_wrapper

    source = make_video(tmp_path, real_tools)
    original = source.read_bytes()
    marker = tmp_path / "decode-pid"
    wrapper = write_wrapper(
        tmp_path / "slow-decode",
        f"""
import os, pathlib, time
pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
time.sleep(30)
""",
    )
    processor = MediaProcessor(
        real_tools.model_copy(update={"ffmpeg_bin": wrapper, "task_timeout_seconds": 1})
    )
    target = tmp_path / "video.mp4"
    with pytest.raises(MediaError) as error:
        asyncio.run(processor.prepare_video(source, target))
    assert error.value.code == "media_timeout"
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
    assert source.read_bytes() == original
    assert not target.exists()
