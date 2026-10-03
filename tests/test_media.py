"""Media boundaries and, when installed, real FFmpeg audio preservation."""

import asyncio

import pytest

from app.services.errors import MediaError


def test_missing_tools_disable_processing_and_leave_input(settings, tmp_path):
    from app.services.media import MediaProcessor

    processor = MediaProcessor(
        settings.model_copy(update={"ffmpeg_bin": "missing-soundpick-ffmpeg"})
    )
    source = tmp_path / "video.mp4"
    source.write_bytes(b"\x00\x00\x00\x18ftypisom")
    target = tmp_path / "audio.mp3"
    assert processor.available is False
    with pytest.raises(MediaError) as error:
        asyncio.run(processor.extract_audio(source, target))
    assert error.value.status == 503
    assert error.value.code == "media_unavailable"
    assert source.exists()
    assert not target.exists()


def test_rejects_text_even_with_video_extension(settings, tmp_path):
    from app.services.media import MediaProcessor

    # Existing executables allow validation to run without requiring FFmpeg here.
    processor = MediaProcessor(
        settings.model_copy(update={"ffmpeg_bin": "/bin/true", "ffprobe_bin": "/bin/true"})
    )
    source = tmp_path / "pretend.mp4"
    source.write_text("not a video")
    target = tmp_path / "audio.mp3"
    with pytest.raises(MediaError) as error:
        asyncio.run(processor.extract_audio(source, target))
    assert error.value.code == "invalid_media"
    assert source.exists()
    assert not target.exists()


def make_tools(settings, tmp_path, *, probe_code=None, convert_code=None):
    """Executable stand-ins exercise the actual OS subprocess boundary."""
    import sys

    if probe_code is None:
        probe_code = """
import json, sys
streams = [{"codec_type": "audio", "duration": "3"}]
if not sys.argv[-1].endswith(".mp3"):
    streams.insert(0, {"codec_type": "video", "duration": "3"})
print(json.dumps({"streams": streams, "format": {"duration": "3"}}))
"""
    if convert_code is None:
        convert_code = """
import pathlib, sys
pathlib.Path(sys.argv[-1]).write_bytes(b"ID3test")
"""
    for name, code in (("probe", probe_code), ("convert", convert_code)):
        tool = tmp_path / name
        readiness = (
            "import sys\n"
            "if '-version' in sys.argv:\n"
            f"    print('{'ffprobe' if name == 'probe' else 'ffmpeg'} version test')\n"
            "    sys.exit(0)\n"
            "if '-encoders' in sys.argv:\n"
            "    print(' A....D libmp3lame Test encoder')\n"
            "    sys.exit(0)\n"
        )
        tool.write_text(f"#!{sys.executable}\n{readiness}{code}")
        tool.chmod(0o700)
    return settings.model_copy(
        update={"ffmpeg_bin": str(tmp_path / "convert"), "ffprobe_bin": str(tmp_path / "probe")}
    )


def make_source(tmp_path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"\x00\x00\x00\x18ftypisom")
    return source


def test_complete_audio_uses_local_protocols_and_first_stream(settings, tmp_path):
    from app.services.media import MediaProcessor

    convert_code = """
import pathlib, sys
args = sys.argv
assert args[args.index("-protocol_whitelist") + 1] == "file,pipe"
assert args[args.index("-map") + 1] == "0:a:0"
assert "-xerror" in args
assert args[args.index("-b:a") + 1] == "192k"
assert args[args.index("-c:a") + 1] == "libmp3lame"
assert not any(arg in args for arg in ("-ss", "-t", "-to", "-af", "-filter:a"))
pathlib.Path(args[-1]).write_bytes(b"ID3test")
"""
    processor = MediaProcessor(make_tools(settings, tmp_path, convert_code=convert_code))
    source = make_source(tmp_path)
    target = tmp_path / "output.mp3"
    assert processor.available
    assert asyncio.run(processor.extract_audio(source, target)) == 3
    assert target.read_bytes() == b"ID3test"
    assert source.exists()


def test_incomplete_result_is_removed_and_error_is_safe(settings, tmp_path):
    from app.services.media import MediaProcessor

    probe = """
import json, sys
output = sys.argv[-1].endswith(".mp3")
print(json.dumps({"streams": [{"codec_type": "video"},
                             {"codec_type": "audio", "duration": "3"}],
                  "format": {"duration": "1" if output else "3"}}))
"""
    processor = MediaProcessor(make_tools(settings, tmp_path, probe_code=probe))
    source = make_source(tmp_path)
    target = tmp_path / "output.mp3"
    with pytest.raises(MediaError) as error:
        asyncio.run(processor.extract_audio(source, target))
    assert error.value.code == "incomplete_audio"
    assert str(tmp_path) not in error.value.message
    assert source.exists()
    assert not target.exists()


@pytest.mark.parametrize(
    "size,code,status", [(0, "empty_media", 400), (1025, "media_size_limit", 413)]
)
def test_source_size_limits(settings, tmp_path, size, code, status):
    from app.services.media import MediaProcessor

    processor = MediaProcessor(
        make_tools(settings, tmp_path).model_copy(update={"max_media_bytes": 1024})
    )
    source = make_source(tmp_path)
    source.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"x" * max(0, size - 12) if size else b"")
    target = tmp_path / "output.mp3"
    with pytest.raises(MediaError) as error:
        asyncio.run(processor.extract_audio(source, target))
    assert (error.value.code, error.value.status) == (code, status)
    assert not target.exists()


@pytest.mark.parametrize(
    "streams,duration,expected",
    [
        ([{"codec_type": "video"}], "3", "missing_audio"),
        ([{"codec_type": "audio"}], "3", "missing_video"),
        ([{"codec_type": "video"}, {"codec_type": "audio"}], "NaN", "invalid_media"),
        ([{"codec_type": "video"}, {"codec_type": "audio"}], "4", "duration_limit"),
        ([{"codec_type": "video"}] * 129, "3", "invalid_media"),
    ],
)
def test_probe_stream_and_duration_boundaries(settings, tmp_path, streams, duration, expected):
    import json

    from app.services.media import MediaProcessor

    probe = f"print({json.dumps({'streams': streams, 'format': {'duration': duration}})!r})"
    processor = MediaProcessor(
        make_tools(settings, tmp_path, probe_code=probe).model_copy(
            update={"max_duration_seconds": 3}
        )
    )
    source = make_source(tmp_path)
    target = tmp_path / "output.mp3"
    with pytest.raises(MediaError) as error:
        asyncio.run(processor.extract_audio(source, target))
    assert error.value.code == expected
    assert not target.exists()


def test_exact_limits_and_audio_duration_over_container(settings, tmp_path):
    from app.services.media import MediaProcessor

    probe = """
import json, sys
output = sys.argv[-1].endswith(".mp3")
print(json.dumps({"streams": [{"codec_type": "video"},
                             {"codec_type": "audio", "duration": "3"}],
                  "format": {"duration": "3" if output else "10"}}))
"""
    configured = make_tools(settings, tmp_path, probe_code=probe).model_copy(
        update={"max_duration_seconds": 10, "max_media_bytes": 1024}
    )
    processor = MediaProcessor(configured)
    source = make_source(tmp_path)
    source.write_bytes(source.read_bytes() + b"x" * (1024 - source.stat().st_size))
    assert asyncio.run(processor.extract_audio(source, tmp_path / "output.mp3")) == 3


@pytest.mark.parametrize(
    "probe_code", ["print('x' * 1000000)", "print('not json')", "print('{\"streams\":42}')"]
)
def test_probe_output_is_bounded_and_must_be_json(settings, tmp_path, probe_code):
    from app.services.media import MediaProcessor

    processor = MediaProcessor(make_tools(settings, tmp_path, probe_code=probe_code))
    with pytest.raises(MediaError) as error:
        asyncio.run(processor.extract_audio(make_source(tmp_path), tmp_path / "output.mp3"))
    assert error.value.code == "invalid_media"


def test_converter_failure_removes_partial_and_hides_stderr(settings, tmp_path):
    from app.services.media import MediaProcessor

    code = """
import pathlib, sys
pathlib.Path(sys.argv[-1]).write_bytes(b"partial")
print("secret token and private path", file=sys.stderr)
sys.exit(1)
"""
    processor = MediaProcessor(make_tools(settings, tmp_path, convert_code=code))
    target = tmp_path / "output.mp3"
    with pytest.raises(MediaError) as error:
        asyncio.run(processor.extract_audio(make_source(tmp_path), target))
    assert error.value.code == "conversion_failed"
    assert "secret" not in str(error.value)
    assert not target.exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_and_cancel_kill_and_reap_process(settings, tmp_path, cancel):
    import os

    from app.services.media import MediaProcessor

    marker = tmp_path / "pid"
    code = f"""
import os, pathlib, sys, time
pathlib.Path(sys.argv[-1]).write_bytes(b"partial")
pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
time.sleep(30)
"""
    configured = make_tools(settings, tmp_path, convert_code=code).model_copy(
        update={"task_timeout_seconds": 1}
    )
    processor = MediaProcessor(configured)
    source = make_source(tmp_path)
    target = tmp_path / "output.mp3"

    async def run():
        task = asyncio.create_task(processor.extract_audio(source, target))
        if cancel:
            async with asyncio.timeout(2):
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


def real_video(tmp_path, configured, *, audio=True):
    """Four-second movie: first audio has 440/220/880 Hz start/middle/end markers."""
    import math
    import shutil
    import struct
    import subprocess
    import wave

    movie = tmp_path / "real.mp4"
    command = [
        shutil.which(configured.ffmpeg_bin),
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=160x120:r=10:d=4",
    ]
    if audio:
        wav = tmp_path / "markers.wav"
        with wave.open(str(wav), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            frames = bytearray()
            for index in range(4 * 16000):
                second = index / 16000
                frequency = 440 if second < 0.6 else (880 if second >= 3.4 else 220)
                frames.extend(
                    struct.pack("<h", int(14000 * math.sin(2 * math.pi * frequency * second)))
                )
            stream.writeframes(frames)
        command += [
            "-i",
            str(wav),
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1200:duration=4",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:a:0",
            "-c:a",
            "aac",
        ]
    command += ["-c:v", "mpeg4", "-shortest", str(movie)]
    subprocess.run(
        command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=15
    )
    return movie


def test_real_ffmpeg_preserves_first_audio_start_and_end(real_tools, tmp_path):
    import math
    import shutil
    import struct
    import subprocess

    from app.services.media import MediaProcessor

    source = real_video(tmp_path, real_tools)
    target = tmp_path / "complete.mp3"
    duration = asyncio.run(MediaProcessor(real_tools).extract_audio(source, target))
    assert abs(duration - 4) <= 0.25
    decoded = subprocess.run(
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
        check=True,
        capture_output=True,
        timeout=15,
    ).stdout
    samples = struct.unpack(f"<{len(decoded) // 2}h", decoded)
    assert abs(len(samples) / 16000 - 4) <= 0.25

    def energy(start, end, frequency):
        segment = samples[int(start * 16000) : int(end * 16000)]
        real = sum(
            value * math.cos(2 * math.pi * frequency * index / 16000)
            for index, value in enumerate(segment)
        )
        imaginary = sum(
            value * math.sin(2 * math.pi * frequency * index / 16000)
            for index, value in enumerate(segment)
        )
        return real * real + imaginary * imaginary

    assert energy(0.1, 0.5, 440) > 10 * energy(0.1, 0.5, 1200)
    assert energy(1.8, 2.2, 220) > 10 * energy(1.8, 2.2, 1200)
    assert energy(3.5, 3.9, 880) > 10 * energy(3.5, 3.9, 1200)
    assert source.exists()


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("silent", "missing_audio"),
        ("damaged", "conversion_failed"),
        ("duration", "duration_limit"),
        ("size", "media_size_limit"),
    ],
)
def test_real_media_failure_cases(real_tools, tmp_path, scenario, code):
    from app.services.media import MediaProcessor

    if scenario == "damaged":
        source = make_source(tmp_path)
    else:
        source = real_video(tmp_path, real_tools, audio=scenario != "silent")
    if scenario == "duration":
        real_tools = real_tools.model_copy(update={"max_duration_seconds": 1})
    elif scenario == "size":
        real_tools = real_tools.model_copy(update={"max_media_bytes": 1024})
    target = tmp_path / "failed.mp3"
    with pytest.raises(MediaError) as error:
        asyncio.run(MediaProcessor(real_tools).extract_audio(source, target))
    assert error.value.code == code
    assert not target.exists()


def test_real_ffmpeg_cancel_removes_output_and_reaps(real_tools, tmp_path):
    import os
    import shutil
    import sys

    from app.services.media import MediaProcessor

    source = real_video(tmp_path, real_tools)
    executable = shutil.which(real_tools.ffmpeg_bin)
    wrapper = tmp_path / "slow-ffmpeg"
    marker = tmp_path / "real-pid"
    wrapper.write_text(f"""#!{sys.executable}
import os, pathlib, sys
pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))
args = sys.argv[1:]
args.insert(args.index("-i"), "-re")
os.execv({executable!r}, [{executable!r}, *args])
""")
    wrapper.chmod(0o700)
    processor = MediaProcessor(real_tools.model_copy(update={"ffmpeg_bin": str(wrapper)}))
    target = tmp_path / "cancelled.mp3"

    async def run():
        task = asyncio.create_task(processor.extract_audio(source, target))
        async with asyncio.timeout(5):
            while not target.exists():
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
    assert source.exists()
    assert not target.exists()
