#!/usr/bin/env python3
"""Disposable Docker + fresh Redis acceptance; never load the project's .env.

Run from WSL or Linux with a working Docker daemon and Compose v2. All persistent
outputs are sanitized JSON. The image, containers, volumes, network and generated
credentials belong to a unique project and are removed in finally, also on failure.
No remote platform is contacted. This is real local runtime evidence, not CI.
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_DOCKER = "/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"
STAGES = (
    "docker_available",
    "compose_config",
    "redis_pull",
    "app_build",
    "stack_healthy",
    "container_isolation",
    "media_fixture",
    "redis_sdk",
    "http_media",
    "failure_release",
    "redis_outage_recovery",
    "redis_aof_restart",
    "app_quota_restart",
    "http_logging",
    "structured_logs",
    "cleanup",
)

# Executed inside the real image, via stdin; dependencies come from requirements.lock.
REDIS_SDK = r"""
import json, os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from redis import Redis
from app.services.errors import MediaError
from app.services.redis_protection import RedisBackend
url = os.environ['REDIS_URL']
base = os.environ['REDIS_KEY_PREFIX'] + ':sdk'
client = Redis.from_url(url, decode_responses=True, socket_timeout=1)
assert client.ping()
assert client.info('persistence')['aof_enabled'] == 1
redis_version = client.info('server')['redis_version']
backend = RedisBackend(url, key_prefix=base, timeout_seconds=1)
backend.quota('reserve', 'release')
assert backend.quota('snapshot')['release'] == 'pending'
backend.quota('release', 'release')
assert 'release' not in backend.quota('snapshot')
def reserve(i):
    item = RedisBackend(url, key_prefix=base + ':atomic', timeout_seconds=1)
    try:
        item.quota('reserve', str(i))
        return True
    except MediaError as error:
        assert error.code == 'daily_quota'
        return False
    finally:
        item.close()
with ThreadPoolExecutor(max_workers=20) as pool:
    accepted = sum(pool.map(reserve, range(20)))
assert accepted == 10
atomic = RedisBackend(url, key_prefix=base + ':atomic', timeout_seconds=1)
assert len(atomic.quota('snapshot')) == 10
# 23:59:59 Beijing, then midnight: prior successes stop consuming today's quota.
now = datetime(2026, 1, 1, 23, 59, 59, tzinfo=timezone(timedelta(hours=8))).timestamp()
clock = [now]
daily = RedisBackend(url, key_prefix=base + ':day', timeout_seconds=1, clock=lambda: clock[0])
for i in range(10):
    daily.quota('reserve', str(i)); daily.quota('succeed', str(i))
try:
    daily.quota('reserve', 'eleven')
except MediaError as error:
    assert error.code == 'daily_quota' and error.retry_after == 1
else:
    raise AssertionError('quota_not_enforced')
clock[0] += 1
daily.quota('reserve', 'next-day'); daily.quota('succeed', 'next-day')
snapshot = daily.quota('snapshot')
assert snapshot['0'] == '2026-01-01' and snapshot['next-day'] == '2026-01-02'
for item in (backend, atomic, daily):
    item.close()
client.close()
print(json.dumps({'ping': True, 'aof_enabled': True, 'failure_release': True,
                  'atomic_accepted': accepted, 'beijing_midnight': True,
                  'redis_version': redis_version}))
"""

MEDIA_FIXTURE = r"""
import base64, json, subprocess
from pathlib import Path
path = Path('/tmp/docker-acceptance.mp4')
subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
    'color=c=blue:s=160x120:r=15:d=2', '-f', 'lavfi', '-i',
    'sine=frequency=440:sample_rate=44100:duration=2', '-f', 'lavfi', '-i',
    'sine=frequency=880:sample_rate=44100:duration=2', '-map', '0:v', '-map', '1:a',
    '-map', '2:a', '-c:v', 'mpeg4', '-c:a', 'aac', '-shortest', str(path)], check=True)
probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
    '-show_format', '-of', 'json', str(path)]))
assert sum(s['codec_type'] == 'audio' for s in probe['streams']) == 2
assert 1.9 <= float(probe['format']['duration']) <= 2.2
print(json.dumps({'data': base64.b64encode(path.read_bytes()).decode(),
                  'duration_seconds': float(probe['format']['duration']), 'audio_tracks': 2}))
path.unlink()
"""

# Downloaded MP3 is decoded by the container's real FFmpeg. Tone evidence distinguishes
# first source track (440 Hz) from the second (880 Hz), at start/middle/end.
AUDIO_ORACLE = r"""
import array, base64, json, math, subprocess, sys
payload = base64.b64decode(sys.stdin.buffer.read())
pcm = subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-i', 'pipe:0',
    '-f', 's16le', '-ac', '1', '-ar', '16000', 'pipe:1'], input=payload)
samples = array.array('h', pcm)
if sys.byteorder != 'little': samples.byteswap()
seconds = len(samples) / 16000
assert 1.9 <= seconds <= 2.3
ratios = []
for center in (0.3, 1.0, 1.7):
    part = samples[int((center - .1) * 16000):int((center + .1) * 16000)]
    energies = []
    for hz in (440, 880):
        real = sum(v * math.cos(2 * math.pi * hz * i / 16000) for i, v in enumerate(part))
        imag = sum(v * math.sin(2 * math.pi * hz * i / 16000) for i, v in enumerate(part))
        energies.append(real * real + imag * imag)
    ratios.append(energies[0] / max(1, energies[1]))
assert min(ratios) > 10
print(json.dumps({'duration_seconds': seconds, 'first_track_verified': True,
                  'start_middle_end_tone_ratios': [round(v, 2) for v in ratios]}))
"""


class CheckFailure(Exception):
    """Only fixed, non-secret categories are attached to failures."""


class Browser:
    def __init__(self, run: DockerCheck):
        self.run = run
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.token = ""

    def request(self, method: str, path: str, data: bytes | None = None, **headers):
        request = urllib.request.Request(
            self.run.base_url + path, data=data, headers=headers, method=method
        )
        try:
            response = self.opener.open(request, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            result = (response.status, response.headers, response.read())
        for cookie in self.jar:
            self.run.sensitive.add(cookie.value)
        return result

    def csrf(self, page: bytes) -> str:
        match = re.search(rb'name="csrf_token" value="([a-f0-9]+)"', page)
        if not match:
            raise CheckFailure("csrf_missing")
        token = match.group(1).decode()
        self.run.sensitive.add(token)
        return token

    def login(self):
        status, _, page = self.request("GET", "/login")
        require(status == 200, "login_page")
        form = urllib.parse.urlencode(
            {"password": self.run.password, "csrf_token": self.csrf(page)}
        ).encode()
        status, _, page = self.request(
            "POST", "/login", form, **{"Content-Type": "application/x-www-form-urlencoded"}
        )
        require(status == 200, "login_success")
        self.token = self.csrf(page)

    def write(self, path: str, body: bytes, content_type="application/octet-stream"):
        return self.request(
            "POST", path, body, **{"X-CSRF-Token": self.token, "Content-Type": content_type}
        )


def require(condition: bool, reason: str):
    if not condition:
        raise CheckFailure(reason)


class DockerCheck:
    def __init__(self, timeout: int):
        self.nonce = secrets.token_hex(6)
        self.project = f"soundpick-local-redis-{self.nonce}"
        self.image = f"soundpick-local-test:{self.nonce}"
        self.output = ROOT / "work" / "runtime" / f"docker-local-redis-{self.nonce}"
        self.output.mkdir(parents=True, mode=0o700)
        self.output.chmod(0o700)
        self.password = secrets.token_urlsafe(24)
        self.session = secrets.token_hex(32)
        self.sensitive = {self.password, self.session}
        self.secret_file = self.output / ".credentials.env"
        self.empty_env = self.output / ".compose-test.env"
        self.private_write(
            self.secret_file, f"APP_PASSWORD={self.password}\nSESSION_SECRET={self.session}\n"
        )
        self.private_write(self.empty_env, "")
        self.env = os.environ.copy()
        for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
            self.env.pop(key, None)
        self.docker = ""
        self.windows = False
        self.base_url = ""
        self.timeout = timeout
        self.started = False
        self.built = False
        self.last_command_failure = None
        self.task_initial_request_ids = {}
        self.logging_request_ids = {}
        self.report = {
            "status": "RUNNING",
            "project": self.project,
            "image_tag": self.image,
            "started_at_utc": datetime.now(UTC).isoformat(),
            "evidence": "local_docker_runtime",
            "stages": {name: {"status": "NOT_RUN"} for name in STAGES},
            "source_sha256": {
                str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (ROOT / "app").rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            },
        }
        self.save()

    @staticmethod
    def private_write(path: Path, value: str):
        # O_EXCL prevents accidentally replacing any existing credential file.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(value)

    def save(self):
        path = self.output / "report.json"
        path.write_text(json.dumps(self.report, ensure_ascii=False, indent=2) + "\n")
        path.chmod(0o600)

    def command(self, args: list[str], *, data=None, timeout=60, check=True):
        result = subprocess.run(
            [self.docker, *args], input=data, capture_output=True, timeout=timeout, env=self.env
        )
        if check and result.returncode:
            # Docker/HTTP exceptions can contain paths, URLs or credentials. Do not print them.
            self.last_command_failure = {
                "exit_code": result.returncode,
                "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
                "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
            }
            raise CheckFailure("docker_command_failed")
        return result

    def path(self, value: Path) -> str:
        if not self.windows:
            return str(value)
        result = subprocess.run(["wslpath", "-w", str(value)], capture_output=True, check=True)
        return result.stdout.decode().strip()

    def compose(self, args, **kwargs):
        return self.command(
            [
                "compose",
                "--project-name",
                self.project,
                "--project-directory",
                self.path(ROOT),
                "--env-file",
                self.path(self.empty_env),
                "--file",
                self.path(ROOT / "compose.test.yml"),
                *args,
            ],
            **kwargs,
        )

    def stage(self, name, operation):
        started = time.monotonic()
        self.report["stages"][name] = {"status": "RUNNING"}
        self.save()
        print(json.dumps({"stage": name, "status": "RUNNING"}), flush=True)
        try:
            details = operation() or {}
            self.report["stages"][name] = {"status": "PASS", **details}
        except BaseException as error:
            self.report["stages"][name] = {
                "status": "FAIL",
                "reason": str(error) if isinstance(error, CheckFailure) else type(error).__name__,
            }
            if isinstance(error, CheckFailure) and str(error) == "docker_command_failed":
                self.report["stages"][name]["command_failure"] = self.last_command_failure
            raise
        finally:
            self.report["stages"][name]["elapsed_seconds"] = round(time.monotonic() - started, 2)
            self.save()
            print(json.dumps({"stage": name, **self.report["stages"][name]}), flush=True)

    def discover(self):
        candidates = [shutil.which("docker"), WINDOWS_DOCKER]
        for candidate in dict.fromkeys(value for value in candidates if value):
            try:
                self.docker = candidate
                server = self.command(["version", "--format", "{{.Server.Version}}"], timeout=15)
                version = self.command(["compose", "version", "--short"], timeout=15)
                self.windows = candidate.endswith(".exe")
                # Compose reads these explicitly on Linux and Windows. WSL's
                # interop need not forward arbitrary Linux environment variables.
                self.empty_env.write_text(
                    f"SOUNDPICK_TEST_IMAGE={self.image}\n"
                    f"SOUNDPICK_TEST_ENV='{self.path(self.secret_file)}'\n"
                )
                self.env.pop("SOUNDPICK_TEST_IMAGE", None)
                self.env.pop("SOUNDPICK_TEST_ENV", None)
                return {
                    "cli": "windows_wsl" if self.windows else "native",
                    "server_version": server.stdout.decode().strip(),
                    "compose_version": version.stdout.decode().strip(),
                }
            except (OSError, subprocess.SubprocessError, CheckFailure):
                continue
        self.docker = ""
        raise CheckFailure("docker_daemon_or_compose_unavailable")

    def container(self, service: str) -> str:
        ids = self.compose(["ps", "--all", "--quiet", service]).stdout.decode().strip().splitlines()
        require(len(ids) == 1 and bool(re.fullmatch(r"[a-f0-9]{12,64}", ids[0])), "container_count")
        return ids[0]

    def inspect(self, service):
        item = json.loads(self.command(["inspect", self.container(service)]).stdout)[0]
        require(
            item["Config"]["Labels"].get("com.docker.compose.project") == self.project,
            "container_ownership",
        )
        return item

    def wait_healthy(self, service):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            item = self.inspect(service)
            if item["State"].get("Health", {}).get("Status") == "healthy":
                return
            require(item["State"]["Running"], "container_stopped")
            time.sleep(1)
        raise CheckFailure("health_timeout")

    def python(self, code: str, *args, data=None):
        # -c is only used when stdin carries media. No private data is an argv value.
        command = ["exec", "-i", self.container("app"), "python"]
        command += ["-c", code, *args] if data is not None else ["-", *args]
        result = self.command(command, data=data if data is not None else code.encode(), timeout=60)
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise CheckFailure("container_helper_output_invalid") from None

    def stack(self):
        # Explicit pulls/builds are separate evidence. No implicit deployment file inheritance.
        self.started = True
        self.compose(["up", "-d", "--no-build", "--pull", "never"], timeout=120)
        self.wait_healthy("redis")
        self.wait_healthy("app")
        return {"loopback_port": self.endpoint("initial")}

    def endpoint(self, phase: str):
        # Docker may allocate a new ephemeral host port when restarting the
        # same container. Always discover and validate the current binding.
        app = self.inspect("app")
        ports = app["NetworkSettings"]["Ports"]["8000/tcp"]
        self.report.setdefault("port_binding", {})[phase] = {
            "configured": app["HostConfig"]["PortBindings"].get("8000/tcp"),
            "actual": ports,
        }
        require(len(ports) == 1 and ports[0]["HostIp"] == "127.0.0.1", "loopback_binding")
        self.base_url = f"http://127.0.0.1:{int(ports[0]['HostPort'])}"
        return int(ports[0]["HostPort"])

    def isolation(self):
        app, redis = self.inspect("app"), self.inspect("redis")
        host = app["HostConfig"]
        env = dict(entry.split("=", 1) for entry in app["Config"]["Env"])
        require(app["Config"]["User"] not in ("", "0", "root"), "nonroot_user")
        require(
            host["ReadonlyRootfs"]
            and host["Memory"] == 512 * 1024**2
            and host["NanoCpus"] == 1_000_000_000
            and host["Init"]
            and "ALL" in host["CapDrop"]
            and "/tmp" in host["Tmpfs"]
            and "no-new-privileges:true" in host["SecurityOpt"]
            and host["LogConfig"]["Config"]["max-file"] == "3",
            "app_security_limits",
        )
        require(
            env["REDIS_URL"] == "redis://redis:6379/0"
            and env["APP_ENV"] == "local"
            and env["COOKIE_SECURE"] == "false",
            "isolated_configuration",
        )
        require(not any(redis["NetworkSettings"]["Ports"].values()), "redis_not_published")
        network_ids = {entry["NetworkID"] for entry in app["NetworkSettings"]["Networks"].values()}
        require(len(network_ids) == 1, "private_network_count")
        network = json.loads(self.command(["network", "inspect", network_ids.pop()]).stdout)[0]
        require(
            network["Driver"] == "bridge"
            and network["Labels"].get("com.docker.compose.project") == self.project
            and set(network["Containers"]) == {app["Id"], redis["Id"]},
            "private_network",
        )
        proof = self.python(
            "import hashlib, json, os, platform, subprocess; from pathlib import Path; "
            "files={str(p.relative_to('/app')): hashlib.sha256(p.read_bytes()).hexdigest() "
            "for p in Path('/app/app').rglob('*') if p.is_file() "
            "and '__pycache__' not in p.parts}; "
            "print(json.dumps({'uid': os.getuid(), 'dotenv_absent': "
            "not Path('/app/.env').exists(), 'source_sha256': files, "
            "'python_version': platform.python_version(), 'ffmpeg_version': "
            "subprocess.check_output(['ffmpeg', '-version']).decode().splitlines()[0]}))"
        )
        require(proof["uid"] != 0 and proof["dotenv_absent"], "image_security")
        require(proof["source_sha256"] == self.report["source_sha256"], "image_source_mismatch")
        return {
            "nonroot": True,
            "rootfs_readonly": True,
            "redis_host_port": False,
            "private_bridge": True,
            "runtime_limits": True,
            "image_source_matches_workspace": True,
            "image_id": app["Image"],
            "python_version": proof["python_version"],
            "ffmpeg_version": proof["ffmpeg_version"],
        }

    def fixture(self):
        import base64

        result = self.python(MEDIA_FIXTURE)
        self.media = base64.b64decode(result.pop("data"), validate=True)
        return {**result, "size": len(self.media), "sha256": hashlib.sha256(self.media).hexdigest()}

    def snapshot(self):
        return self.python(
            "import json, os; from app.services.redis_protection import RedisBackend; "
            "b=RedisBackend(os.environ['REDIS_URL'], "
            "key_prefix=os.environ['REDIS_KEY_PREFIX'], timeout_seconds=1); "
            "print(json.dumps(b.quota('snapshot'))); b.close()"
        )

    def upload(self, browser: Browser, media: bytes):
        task_id = secrets.token_hex(16)
        body = json.dumps({"task_id": task_id, "filename": "本地验收.mp4"}).encode()
        status, headers, _ = browser.write("/api/tasks/reservations", body, "application/json")
        require(status == 202, "reserve_accepted")
        self.task_initial_request_ids[task_id] = headers.get("X-Request-ID")
        status, _, _ = browser.write(f"/api/tasks/{task_id}/upload", media)
        require(status == 202, "upload_accepted")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status, _, body = browser.request("GET", f"/api/tasks/{task_id}")
            require(status == 200, "poll_status")
            task = json.loads(body)
            if task["state"] in ("ready", "failed", "cancelled"):
                return task
            time.sleep(1)
        raise CheckFailure("media_timeout")

    def functional(self):
        import base64

        self.owner = Browser(self)
        self.owner.login()
        status, _, body = self.owner.request("GET", "/api/capabilities")
        capabilities = json.loads(body)
        require(status == 200 and capabilities["media_available"], "real_media_available")
        task = self.upload(self.owner, self.media)
        require(task["state"] == "ready", "media_ready")
        self.success_ids = {task["id"]}
        audio = b""
        for kind, url in (("mp3", task["download_url"]), ("mp4", task["video_download_url"])):
            status, headers, data = self.owner.request("GET", url)
            require(status == 200 and data, "download_success")
            if kind == "mp4":
                require(data == self.media, "original_mp4_preserved")
            else:
                require(headers.get("Content-Type") == "audio/mpeg", "mp3_content_type")
                audio = data
            status, headers, head = self.owner.request("HEAD", url)
            require(
                status == 200 and not head and int(headers["Content-Length"]) == len(data),
                "download_head",
            )
            status, headers, chunk = self.owner.request("GET", url, **{"Range": "bytes=1-127"})
            require(
                status == 206
                and chunk == data[1:128]
                and headers["Content-Range"] == f"bytes 1-127/{len(data)}",
                "download_range",
            )
        peer = Browser(self)
        peer.login()
        require(
            all(
                peer.request("GET", url)[0] == 404
                for url in (task["download_url"], task["video_download_url"])
            ),
            "cross_session_denied",
        )
        self.raw_share = "private-share-input-" + secrets.token_hex(16)
        self.sensitive.add(self.raw_share)
        status, _, _ = self.owner.write(
            "/api/tasks/link",
            json.dumps({"share_text": self.raw_share}).encode(),
            "application/json",
        )
        require(status == 400, "invalid_share_local_only")
        oracle = self.python(AUDIO_ORACLE, data=base64.b64encode(audio))
        snapshot = self.snapshot()
        require(task["id"] in snapshot and snapshot[task["id"]] != "pending", "successful_quota")
        return {
            "mp3": oracle,
            "mp4_original_bytes": True,
            "head_range": True,
            "cross_session_denied": True,
            "successful_quota_count": 1,
        }

    def failed_media(self):
        task = self.upload(self.owner, b"not-a-valid-video-container")
        require(task["state"] == "failed" and not task["download_url"], "invalid_media_failed")
        require(task["id"] not in self.snapshot(), "failed_quota_released")
        return {"terminal_state": "failed", "pending_released": True}

    def outage(self):
        self.compose(["stop", "redis"], timeout=30)
        task = self.upload(self.owner, self.media)
        require(task["state"] == "ready", "outage_media_ready")
        self.success_ids.add(task["id"])
        self.compose(["start", "redis"], timeout=30)
        self.wait_healthy("redis")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            # The actual running app must retry and merge; a separate SDK cannot do this for it.
            status, _, _ = self.owner.request("GET", "/api/capabilities")
            require(status == 200, "recovery_request")
            if self.success_ids.issubset(self.snapshot()):
                break
            time.sleep(1)
        else:
            raise CheckFailure("outage_success_not_merged")
        return {"media_during_outage": True, "retry_merged_successes": len(self.success_ids)}

    def redis_restart(self):
        before = self.snapshot()
        self.compose(["restart", "redis"], timeout=45)
        self.wait_healthy("redis")
        require(self.snapshot() == before, "aof_snapshot_restored")
        return {"aof_snapshot_restored": True, "success_count": len(self.success_ids)}

    def app_restart(self):
        before = self.snapshot()
        self.compose(["restart", "app"], timeout=45)
        self.wait_healthy("app")
        loopback_port = self.endpoint("after_app_restart")
        require(self.snapshot() == before, "app_restart_quota_restored")
        # A reservation by the restarted process proves the server can consume the persisted state.
        browser = Browser(self)
        browser.login()
        task_id = secrets.token_hex(16)
        status, _, _ = browser.write(
            "/api/tasks/reservations",
            json.dumps({"task_id": task_id, "filename": "重启验收.mp4"}).encode(),
            "application/json",
        )
        require(
            status == 202 and self.snapshot().get(task_id) == "pending", "restart_pending_quota"
        )
        status, _, _ = browser.write(f"/api/tasks/{task_id}/cancel", b"")
        require(status == 200 and self.snapshot() == before, "restart_cancel_release")
        return {
            "success_quota_restored": True,
            "pending_reserve_cancel": True,
            "loopback_port": loopback_port,
        }

    def http_logging(self):
        browser = Browser(self)
        browser.login()
        query = "private-query-" + secrets.token_hex(16)
        body_marker = "private-body-" + secrets.token_hex(16)
        header = "private-header-" + secrets.token_hex(16)
        supplied_request_id = secrets.token_hex(16)
        self.sensitive.update((query, body_marker, header, supplied_request_id))
        oversized = (body_marker + "x" * 16385).encode()
        status, headers, _ = browser.request(
            "POST",
            "/logout?token=" + query,
            oversized,
            **{
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(oversized)),
                "X-Private-Test": header,
                "X-Request-ID": supplied_request_id,
            },
        )
        request_id = headers.get("X-Request-ID", "")
        require(
            status == 413
            and bool(re.fullmatch(r"[a-f0-9]{32}", request_id))
            and request_id != supplied_request_id,
            "oversized_request_correlation",
        )
        self.logging_request_ids[request_id] = {"status_code": 413, "error_category": "size_limit"}
        seen_ids = {request_id}
        rejected = 0
        attempted = 0
        for _ in range(9):
            attempted += 1
            task_id = secrets.token_hex(16)
            status, headers, body = browser.write(
                "/api/tasks/reservations",
                json.dumps({"task_id": task_id, "filename": "限流日志验收.mp4"}).encode(),
                "application/json",
            )
            request_id = headers.get("X-Request-ID", "")
            require(
                bool(re.fullmatch(r"[a-f0-9]{32}", request_id)) and request_id not in seen_ids,
                "rate_request_id_unique",
            )
            seen_ids.add(request_id)
            if status == 202:
                self.task_initial_request_ids[task_id] = request_id
                status, _, _ = browser.write(f"/api/tasks/{task_id}/cancel", b"")
                require(status == 200, "rate_test_reservation_released")
            elif status == 429:
                response = json.loads(body)
                wait = int(headers.get("Retry-After", "0"))
                require(
                    response.get("code") == "rate_limit"
                    and wait > 0
                    and response.get("retry_after") == wait,
                    "rate_response_retry",
                )
                self.logging_request_ids[request_id] = {
                    "status_code": 429,
                    "error_category": "rate_limit",
                    "retry_after_seconds": wait,
                }
                rejected += 1
                if rejected >= 2:
                    break
            else:
                raise CheckFailure("rate_test_unexpected_status")
        require(rejected >= 2, "rate_limit_not_observed_twice")
        return {
            "size_limit_413": True,
            "rate_limit_429_count": rejected,
            "reservation_attempts": attempted,
            "request_ids_unique": True,
            "client_request_id_ignored": True,
            "retry_after_consistent": True,
        }

    def logs(self):
        result = self.compose(["logs", "--no-color", "--no-log-prefix", "app"])
        raw = result.stdout + result.stderr
        decoded = raw.decode(errors="replace")
        require(not any(value in decoded for value in self.sensitive if value), "log_secret_leak")
        events = Counter()
        entries = []
        non_json_lines = 0
        for line in decoded.splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                non_json_lines += 1
                continue
            if isinstance(entry, dict) and isinstance(entry.get("event"), str):
                events[entry["event"]] += 1
                entries.append(entry)
            else:
                non_json_lines += 1
        # Unknown lines (including stderr) are summarized by count/hash, never persisted raw.
        self.report["log_capture"] = {
            "non_json_lines": non_json_lines,
            "stderr_bytes": len(result.stderr),
            "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
        }
        require(non_json_lines == 0, "unknown_non_json_log_lines")
        expected = (
            "application.started",
            "task.stage",
            "task.failed",
            "protection.redis_degraded",
            "protection.redis_connected",
            "request.completed",
        )
        require(all(events[event] for event in expected), "structured_event_missing")
        completed = {
            entry.get("request_id"): entry
            for entry in entries
            if entry["event"] == "request.completed"
        }
        verified_errors = Counter()
        for request_id, fields in self.logging_request_ids.items():
            entry = completed.get(request_id)
            # Repeated 429s may be deliberately suppressed; the first must remain visible.
            if entry is None and fields["status_code"] == 429:
                continue
            require(
                entry is not None and all(entry.get(key) == value for key, value in fields.items()),
                "request_error_log_fields",
            )
            verified_errors[fields["status_code"]] += 1
        require(
            verified_errors[413] >= 1 and verified_errors[429] >= 1, "request_error_logs_missing"
        )
        created = {
            entry.get("task_id"): entry.get("request_id")
            for entry in entries
            if entry["event"] == "task.created"
        }
        for task_id, request_id in self.task_initial_request_ids.items():
            require(
                bool(re.fullmatch(r"[a-f0-9]{32}", request_id or ""))
                and created.get(task_id) == request_id,
                "task_initial_request_correlation",
            )
        stages = [entry for entry in entries if entry["event"] in ("task.stage", "task.failed")]
        require(
            stages
            and all(
                entry.get("task_id") in created
                and entry.get("request_id") == created[entry["task_id"]]
                for entry in stages
            ),
            "task_stage_request_correlation",
        )
        safe_logs = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)
        self.private_write(self.output / "app.jsonl", safe_logs)
        return {
            "event_counts": {event: events[event] for event in expected},
            "secret_scan_passed": True,
            "log_sha256": hashlib.sha256(raw).hexdigest(),
            "non_json_lines": non_json_lines,
            "error_413_429_fields_verified": True,
            "task_request_correlation_verified": True,
            "safe_log_file": "app.jsonl",
        }

    def cleanup(self):
        errors = []
        try:
            if self.docker and self.started:
                result = self.compose(
                    ["down", "--volumes", "--timeout", "10"], timeout=90, check=False
                )
                if result.returncode:
                    errors.append("compose_down_failed")
                for kind in ("container", "network", "volume"):
                    result = self.command(
                        [
                            kind,
                            "ls",
                            *(["--all"] if kind == "container" else []),
                            "--quiet",
                            "--filter",
                            f"label=com.docker.compose.project={self.project}",
                        ],
                        check=False,
                    )
                    if result.returncode or result.stdout.strip():
                        errors.append(f"{kind}_cleanup_unverified")
            if self.docker and self.built:
                image = self.command(["image", "ls", "--quiet", self.image], check=False)
                if image.returncode:
                    errors.append("test_image_cleanup_unverified")
                elif image.stdout.strip():
                    if self.command(
                        ["image", "rm", self.image], timeout=60, check=False
                    ).returncode:
                        errors.append("test_image_cleanup_failed")
        finally:
            self.secret_file.unlink(missing_ok=True)
            self.empty_env.unlink(missing_ok=True)
        require(not errors, errors[0] if errors else "cleanup_failed")
        return {"project_resources_removed": True, "temporary_credentials_removed": True}

    def run(self):
        result = 1
        try:
            self.stage("docker_available", self.discover)
            self.stage("compose_config", lambda: self.compose(["config", "--quiet"]) and {})
            self.stage(
                "redis_pull", lambda: self.compose(["pull", "redis"], timeout=self.timeout) and {}
            )
            self.built = True  # A failed build may still leave the unique tag; cleanup attempts it.
            self.stage(
                "app_build", lambda: self.compose(["build", "app"], timeout=self.timeout) and {}
            )
            self.stage("stack_healthy", self.stack)
            self.stage("container_isolation", self.isolation)
            self.stage("media_fixture", self.fixture)
            self.stage("redis_sdk", lambda: self.python(REDIS_SDK))
            self.stage("http_media", self.functional)
            self.stage("failure_release", self.failed_media)
            self.stage("redis_outage_recovery", self.outage)
            self.stage("redis_aof_restart", self.redis_restart)
            self.stage("app_quota_restart", self.app_restart)
            self.stage("http_logging", self.http_logging)
            self.stage("structured_logs", self.logs)
            result = 0
        except BaseException as error:
            self.report["failure_category"] = (
                str(error) if isinstance(error, CheckFailure) else type(error).__name__
            )
        finally:
            try:
                self.stage("cleanup", self.cleanup)
            except BaseException:
                result = 1
            for stage in self.report["stages"].values():
                if stage["status"] == "NOT_RUN":
                    stage.update(status="FAIL", reason="not_run_due_to_prior_failure")
            self.report["status"] = "PASS" if result == 0 else "FAIL"
            self.report["finished_at_utc"] = datetime.now(UTC).isoformat()
            self.save()
            print(
                json.dumps(
                    {"status": self.report["status"], "report": str(self.output / "report.json")}
                ),
                flush=True,
            )
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--docker-timeout",
        type=int,
        default=900,
        help="Seconds allowed for each image pull/build (default: 900)",
    )
    args = parser.parse_args()
    if args.docker_timeout < 30:
        parser.error("--docker-timeout must be at least 30")
    return DockerCheck(args.docker_timeout).run()


if __name__ == "__main__":
    raise SystemExit(main())
