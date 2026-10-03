"""The standard check must fail, rather than silently skip real media."""

import os
import subprocess
import sys


def test_standard_test_runner_rejects_missing_media_tools():
    result = subprocess.run(
        [sys.executable, "-m", "scripts.run_tests"],
        env={
            **os.environ,
            "FFMPEG_BIN": "missing-review-ffmpeg",
            "FFPROBE_BIN": "missing-review-ffprobe",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "不会跳过" in result.stderr
