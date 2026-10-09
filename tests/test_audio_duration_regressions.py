"""Real-media regressions for audio duration independent of the video timeline."""

import asyncio
import json
import math
import os
import shutil
import struct
import subprocess
import wave

import pytest

from app.services.errors import MediaError
from app.services.media import MediaProcessor
from tests.test_media import make_source, make_tools
from tests.test_video import write_wrapper


def make_short_audio_video(configured, tmp_path, *, container="mkv", offset=0, gap=False):
    wav = tmp_path / "markers.wav"
    samples = [
        int(14000 * math.sin(2 * math.pi * (440 if i < 16000 else 880) * i / 16000))
        for i in range(32000)
    ]
    with wave.open(str(wav), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    source = tmp_path / f"short-audio.{container}"
    subprocess.run(
        [
            shutil.which(configured.ffmpeg_bin),
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x120:r=10:duration=5",
            "-itsoffset",
            str(offset),
            "-i",
            str(wav),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1200:duration=5",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:a",
            "-c:v",
            "libvpx" if container == "webm" else "mpeg4",
            "-c:a",
            "libopus" if container == "webm" else "aac",
            *(["-filter:a:0", "aselect=not(between(t\\,0.8\\,1.1))"] if gap else []),
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    return source


@pytest.mark.parametrize(
    "container,offset,gap",
    [
        ("mkv", 0, False),
        ("mkv", 1, False),
        ("webm", 0, False),
        ("webm", 1, False),
        ("mkv", 1, True),
        ("mp4", 0, True),
    ],
)
def test_short_first_audio_with_independent_timeline_is_complete(
    real_tools, tmp_path, container, offset, gap
):
    source = make_short_audio_video(
        real_tools, tmp_path, container=container, offset=offset, gap=gap
    )
    info = json.loads(
        subprocess.check_output(
            [
                shutil.which(real_tools.ffprobe_bin),
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,duration,start_time:format=duration",
                "-of",
                "json",
                str(source),
            ],
            timeout=15,
        )
    )
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    if container == "mp4":
        assert float(audio["duration"]) > 1.9
    else:
        assert "duration" not in audio
    if offset:
        assert float(audio["start_time"]) >= 0.8
    assert float(info["format"]["duration"]) > 4.9
    target = tmp_path / "complete.mp3"
    duration = asyncio.run(MediaProcessor(real_tools).extract_audio(source, target))
    expected = 1.7 if gap else 2
    assert abs(duration - expected) <= 0.25
    decoded = subprocess.check_output(
        [
            shutil.which(real_tools.ffmpeg_bin),
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(target),
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            "16000",
            "pipe:1",
        ],
        timeout=15,
    )
    samples = struct.unpack(f"<{len(decoded) // 2}h", decoded)
    assert abs(len(samples) / 16000 - expected) <= 0.25

    def energy(start, end, frequency):
        segment = samples[int(start * 16000) : int(end * 16000)]
        return (
            abs(
                sum(
                    value
                    * complex(
                        math.cos(2 * math.pi * frequency * index / 16000),
                        math.sin(2 * math.pi * frequency * index / 16000),
                    )
                    for index, value in enumerate(segment)
                )
            )
            ** 2
        )

    assert energy(0.2, 0.6, 440) > 10 * energy(0.2, 0.6, 1200)
    assert energy(expected - 0.6, expected - 0.2, 880) > 10 * energy(
        expected - 0.6, expected - 0.2, 1200
    )
    assert source.exists()


@pytest.mark.parametrize("container,gap", [("mkv", False), ("mp4", True)])
def test_sample_duration_still_rejects_truncated_transcode(real_tools, tmp_path, container, gap):
    source = make_short_audio_video(real_tools, tmp_path, container=container, gap=gap)
    executable = shutil.which(real_tools.ffmpeg_bin)
    wrapper = write_wrapper(
        tmp_path / "truncate-mp3",
        f"""
import os, sys
args = sys.argv[1:]
if args[-2] == 'mp3':
    args[-1:-1] = ['-t', '1']
os.execv({executable!r}, [{executable!r}, *args])
""",
    )
    target = tmp_path / "truncated.mp3"
    processor = MediaProcessor(real_tools.model_copy(update={"ffmpeg_bin": wrapper}))
    with pytest.raises(MediaError) as error:
        asyncio.run(processor.extract_audio(source, target))
    assert error.value.code == "incomplete_audio"
    assert source.exists()
    assert not target.exists()


@pytest.mark.parametrize("container", ["mkv", "mp4"])
def test_measured_audio_cannot_exceed_limit_with_understated_metadata(
    real_tools, tmp_path, container
):
    source = make_short_audio_video(real_tools, tmp_path, container=container)
    executable = shutil.which(real_tools.ffprobe_bin)
    wrapper = write_wrapper(
        tmp_path / "understate-container",
        f"""
import json, subprocess, sys
result = json.loads(subprocess.check_output([{executable!r}, *sys.argv[1:]]))
if not sys.argv[-1].endswith('.mp3'):
    result['format']['duration'] = '1'
    for stream in result['streams']:
        if 'duration' in stream:
            stream['duration'] = '1'
print(json.dumps(result))
""",
    )
    target = tmp_path / "overlimit.mp3"
    processor = MediaProcessor(
        real_tools.model_copy(
            update={
                "ffprobe_bin": wrapper,
                "max_duration_seconds": 1,
            }
        )
    )
    with pytest.raises(MediaError) as error:
        asyncio.run(processor.extract_audio(source, target))
    assert error.value.code == "duration_limit"
    assert not target.exists()


@pytest.mark.parametrize("progress", ["print('x' * 1000000)", "print('out_time_us=2000000')"])
def test_sample_measurement_requires_bounded_completed_progress(settings, tmp_path, progress):
    probe = """
import json
print(json.dumps({'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}],
                  'format': {'duration': '3'}}))
"""
    configured = make_tools(settings, tmp_path, probe_code=probe, convert_code=progress)
    target = tmp_path / "output.mp3"
    with pytest.raises(MediaError) as error:
        asyncio.run(MediaProcessor(configured).extract_audio(make_source(tmp_path), target))
    assert error.value.code == "invalid_media"
    assert not target.exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_sample_measurement_timeout_and_cancel_reap_process(settings, tmp_path, cancel):
    marker = tmp_path / "scan-pid"
    probe = """
import json
print(json.dumps({'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}],
                  'format': {'duration': '3'}}))
"""
    scan = f"""
import os, pathlib, sys, time
args = sys.argv
assert args[args.index('-protocol_whitelist') + 1] == 'file,pipe'
assert args[args.index('-map') + 1] == '0:a:0'
assert args[args.index('-af') + 1] == 'asetpts=N/SR/TB'
assert float(args[args.index('-stats_period') + 1]) > 1
assert args[-2:] == ['null', '-']
pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
time.sleep(30)
"""
    configured = make_tools(settings, tmp_path, probe_code=probe, convert_code=scan).model_copy(
        update={"task_timeout_seconds": 1}
    )
    source = make_source(tmp_path)
    target = tmp_path / "output.mp3"

    async def run():
        task = asyncio.create_task(MediaProcessor(configured).extract_audio(source, target))
        if cancel:
            async with asyncio.timeout(3):
                while not marker.exists():
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
