"""Original video preparation, using real media and OS process boundaries."""

import asyncio
import json
import os
import shutil
import subprocess
import sys

import pytest

from app.services.errors import MediaError
from app.services.media import MediaProcessor


def make_video(tmp_path, configured, *, container="mp4", audio_codec="aac"):
    target = tmp_path / f"source.{container}"
    subprocess.run(
        [
            shutil.which(configured.ffmpeg_bin),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:r=10:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=3",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:a",
            "-c:v",
            "mpeg4",
            "-c:a",
            audio_codec,
            "-metadata",
            "title=家庭原视频",
            "-metadata:s:a:0",
            "language=chi",
            "-metadata:s:a:1",
            "language=eng",
            "-disposition:a:0",
            "0",
            "-disposition:a:1",
            "default",
            "-shortest",
            str(target),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return target


def probe(configured, path, *, packets=False):
    args = [
        shutil.which(configured.ffprobe_bin),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
    ]
    if packets:
        args += ["-show_packets", "-show_data_hash", "sha256"]
    return json.loads(
        subprocess.run(
            [*args, str(path)],
            check=True,
            capture_output=True,
            timeout=15,
        ).stdout
    )


def test_real_mp4_keeps_original_bytes(real_tools, tmp_path):
    source = make_video(tmp_path, real_tools)
    original = source.read_bytes()
    target = tmp_path / "video.mp4"
    processor = MediaProcessor(real_tools)
    assert callable(getattr(processor, "prepare_video", None)), "MP4 preparation is missing"
    duration = asyncio.run(processor.prepare_video(source, target))
    assert abs(duration - 3) <= 0.25
    assert target.read_bytes() == original
    assert target.stat().st_mode & 0o777 == 0o600
    assert not source.exists()


def test_real_mov_remux_keeps_packets_duration_and_metadata(real_tools, tmp_path):
    source = make_video(tmp_path, real_tools, container="mov")
    rotated = tmp_path / "rotated.mov"
    subprocess.run(
        [
            shutil.which(real_tools.ffmpeg_bin),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-display_rotation:v:0",
            "90",
            "-i",
            str(source),
            "-map",
            "0",
            "-c",
            "copy",
            str(rotated),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    source = rotated
    original = probe(real_tools, source, packets=True)
    assert source.read_bytes()[8:12] == b"qt  "
    # A misleading .mp4 suffix must still trigger a real MP4 remux.
    source = source.rename(tmp_path / "pretend.mp4")
    before_bytes = source.read_bytes()
    target = tmp_path / "video.mp4"
    duration = asyncio.run(MediaProcessor(real_tools).prepare_video(source, target))
    output = probe(real_tools, target, packets=True)
    assert source.read_bytes() == before_bytes
    assert target.read_bytes()[8:12] != b"qt  "
    assert abs(duration - float(original["format"]["duration"])) <= 0.25
    assert output["format"]["tags"]["title"] == "家庭原视频"
    assert [(s["codec_type"], s["codec_name"]) for s in original["streams"]] == [
        (s["codec_type"], s["codec_name"]) for s in output["streams"]
    ]
    for before, after in zip(original["streams"], output["streams"], strict=True):
        assert abs(float(before["duration"]) - float(after["duration"])) <= 0.25
        assert before["disposition"]["default"] == after["disposition"]["default"]
        if before["codec_type"] == "audio":
            assert before["tags"]["language"] == after["tags"]["language"]
        if before["codec_type"] == "video":
            assert before["side_data_list"][0]["rotation"] == 90
            assert after["side_data_list"][0]["rotation"] == 90
    for index in range(len(original["streams"])):
        assert [p["data_hash"] for p in original["packets"] if p["stream_index"] == index] == [
            p["data_hash"] for p in output["packets"] if p["stream_index"] == index
        ]


def write_wrapper(path, code):
    path.write_text(f"#!{sys.executable}\n{code}")
    path.chmod(0o700)
    return str(path)


def test_real_remux_maps_every_video_and_audio_stream(real_tools, tmp_path):
    source = make_video(tmp_path, real_tools, container="mov")
    multiple = tmp_path / "multiple.mov"
    subprocess.run(
        [
            shutil.which(real_tools.ffmpeg_bin),
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v",
            "-map",
            "0:a",
            "-map",
            "0:v",
            "-c",
            "copy",
            str(multiple),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    target = tmp_path / "video.mp4"
    asyncio.run(MediaProcessor(real_tools).prepare_video(multiple, target))
    before, after = (
        probe(real_tools, multiple, packets=True),
        probe(real_tools, target, packets=True),
    )
    assert len(after["streams"]) == 4
    assert [(s["codec_type"], s["codec_name"]) for s in before["streams"]] == [
        (s["codec_type"], s["codec_name"]) for s in after["streams"]
    ]
    for index in range(4):
        assert [p["data_hash"] for p in before["packets"] if p["stream_index"] == index] == [
            p["data_hash"] for p in after["packets"] if p["stream_index"] == index
        ]


def test_same_input_output_is_rejected_without_deleting_input(real_tools, tmp_path):
    source = make_video(tmp_path, real_tools)
    original = source.read_bytes()
    with pytest.raises(MediaError) as error:
        asyncio.run(MediaProcessor(real_tools).prepare_video(source, source))
    assert error.value.code == "invalid_media"
    assert source.read_bytes() == original


@pytest.mark.parametrize("scenario", ["tools", "size", "duration", "damaged", "unsupported"])
def test_video_errors_preserve_input_and_remove_partial(real_tools, tmp_path, scenario):
    source = make_video(tmp_path, real_tools, container="mov")
    configured = real_tools
    if scenario == "tools":
        configured = configured.model_copy(update={"ffmpeg_bin": "no-soundpick-ffmpeg"})
    elif scenario == "size":
        configured = configured.model_copy(update={"max_media_bytes": 1024})
    elif scenario == "duration":
        configured = configured.model_copy(update={"max_duration_seconds": 1})
    elif scenario == "damaged":
        source.write_bytes(b"\x00\x00\x00\x18ftypisom")
    else:
        source = tmp_path / "unsupported.avi"
        subprocess.run(
            [
                shutil.which(real_tools.ffmpeg_bin),
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=s=160x120:d=1",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=1",
                "-c:v",
                "wmv2",
                "-c:a",
                "pcm_s16le",
                str(source),
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
    target = tmp_path / "video.mp4"
    target.write_bytes(b"old partial")
    with pytest.raises(MediaError) as error:
        asyncio.run(MediaProcessor(configured).prepare_video(source, target))
    assert str(tmp_path) not in error.value.message
    assert source.exists()
    assert not target.exists()


@pytest.mark.parametrize("mode", ["oversize", "wrong_container"])
def test_actual_remux_output_size_and_container_must_be_valid(real_tools, tmp_path, mode):
    source = make_video(tmp_path, real_tools, container="mov")
    ffmpeg = shutil.which(real_tools.ffmpeg_bin)
    wrapper = write_wrapper(
        tmp_path / "convert",
        f"""
import pathlib, subprocess, sys
subprocess.run([{ffmpeg!r}, *sys.argv[1:]], check=True)
target = pathlib.Path(sys.argv[-1])
if {mode!r} == 'oversize':
    with target.open('ab') as stream:
        stream.write(b'x' * (2 * 1024 * 1024))
else:
    target.write_bytes(pathlib.Path({str(source)!r}).read_bytes())
""",
    )
    configured = real_tools.model_copy(
        update={
            "ffmpeg_bin": wrapper,
            "max_media_bytes": source.stat().st_size,
        }
    )
    target = tmp_path / "video.mp4"
    with pytest.raises(MediaError) as error:
        asyncio.run(MediaProcessor(configured).prepare_video(source, target))
    assert error.value.code == "video_unavailable"
    assert source.exists()
    assert not target.exists()


@pytest.mark.parametrize(
    "mode", ["container_duration", "stream_duration", "missing_duration", "missing_stream", "codec"]
)
def test_remux_probe_requires_complete_streams_and_durations(real_tools, tmp_path, mode):
    source = make_video(tmp_path, real_tools, container="mov")
    executable = shutil.which(real_tools.ffprobe_bin)
    wrapper = write_wrapper(
        tmp_path / "probe",
        f"""
import json, subprocess, sys
result = json.loads(subprocess.run([{executable!r}, *sys.argv[1:]],
                                  check=True, capture_output=True).stdout)
if sys.argv[-1].endswith('/video.mp4'):
    mode = {mode!r}
    if mode == 'container_duration': result['format']['duration'] = '1'
    elif mode == 'stream_duration': result['streams'][0]['duration'] = '1'
    elif mode == 'missing_duration': result['streams'][0].pop('duration', None)
    elif mode == 'missing_stream': result['streams'].pop()
    else: result['streams'][0]['codec_name'] = 'h264'
print(json.dumps(result))
""",
    )
    processor = MediaProcessor(real_tools.model_copy(update={"ffprobe_bin": wrapper}))
    target = tmp_path / "video.mp4"
    with pytest.raises(MediaError) as error:
        asyncio.run(processor.prepare_video(source, target))
    assert error.value.code == "incomplete_video"
    assert source.exists()
    assert not target.exists()


@pytest.mark.parametrize("cancel", [True, False])
def test_real_video_cancel_or_timeout_kills_and_reaps(real_tools, tmp_path, cancel):
    source = make_video(tmp_path, real_tools, container="mov")
    executable = shutil.which(real_tools.ffmpeg_bin)
    marker = tmp_path / "pid"
    wrapper = write_wrapper(
        tmp_path / "slow-convert",
        f"""
import os, pathlib, sys
pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
args = sys.argv[1:]
args.insert(args.index('-i'), '-re')
os.execv({executable!r}, [{executable!r}, *args])
""",
    )
    configured = real_tools.model_copy(update={"ffmpeg_bin": wrapper, "task_timeout_seconds": 1})
    processor = MediaProcessor(configured)
    target = tmp_path / "video.mp4"

    async def run():
        task = asyncio.create_task(processor.prepare_video(source, target))
        if cancel:
            async with asyncio.timeout(5):
                while not target.exists():
                    await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(MediaError) as error:
                await task
            assert error.value.code == "media_timeout"

    asyncio.run(run())
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
    assert source.exists()
    assert not target.exists()
