#!/usr/bin/env python3
"""No-secret build readiness checks for KindCaddy.

This runner automates the local portions of deploy/BUILD-READINESS-TEST-PLAN.md.
It intentionally does not read .env or ios/KindCaddy/Secrets.xcconfig. Secret-
dependent behavior is exercised with temporary environment variables and a local
OpenAI-compatible fake server.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import httpx
import jwt


ROOT = Path(__file__).resolve().parents[1]
IOS_PROJECT = ROOT / "ios" / "KindCaddy" / "KindCaddy.xcodeproj"
IOS_SCHEME = "KindCaddy"
DEFAULT_PARS = [4, 4, 4, 3, 5, 4, 4, 3, 5, 4, 4, 4, 3, 5, 4, 4, 3, 5]
SCORES = [5, 5, 5, 4, 6, 5, 5, 4, 6, 5, 5, 5, 4, 6, 5, 5, 4, 6]
READINESS_DB_PATH: Path | None = None
READINESS_JWT_SECRET = "readiness-jwt-secret-not-production"


@dataclass
class CheckResult:
    gate: str
    name: str
    status: str
    detail: str = ""


@dataclass
class Runner:
    results: list[CheckResult] = field(default_factory=list)

    def pass_(self, gate: str, name: str, detail: str = "") -> None:
        self.results.append(CheckResult(gate, name, "PASS", detail))
        print(f"[PASS] {gate}: {name}" + (f" - {detail}" if detail else ""))

    def fail(self, gate: str, name: str, detail: str = "") -> None:
        self.results.append(CheckResult(gate, name, "FAIL", detail))
        print(f"[FAIL] {gate}: {name}" + (f" - {detail}" if detail else ""))

    def manual(self, gate: str, name: str, detail: str = "") -> None:
        self.results.append(CheckResult(gate, name, "MANUAL", detail))
        print(f"[MANUAL] {gate}: {name}" + (f" - {detail}" if detail else ""))

    def check(self, gate: str, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.pass_(gate, name, detail)
        else:
            self.fail(gate, name, detail)

    def run_step(self, gate: str, name: str, func: Callable[[], str | None]) -> None:
        try:
            detail = func() or ""
        except Exception as exc:  # noqa: BLE001 - readiness runner should keep going.
            self.fail(gate, name, str(exc))
        else:
            self.pass_(gate, name, detail)

    def print_summary(self) -> int:
        counts: dict[str, int] = {"PASS": 0, "FAIL": 0, "MANUAL": 0}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1

        print("\n=== Readiness Summary ===")
        for status in ("PASS", "FAIL", "MANUAL"):
            print(f"{status}: {counts.get(status, 0)}")

        failures = [r for r in self.results if r.status == "FAIL"]
        if failures:
            print("\nFailures:")
            for failure in failures:
                print(f"- {failure.gate}: {failure.name}: {failure.detail}")
            return 1
        return 0


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    server_version = "KindCaddyFakeOpenAI/1.0"

    def log_message(self, _fmt: str, *_args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/v1/models":
            self._send_json(200, {"object": "list", "data": [{"id": "gpt-4o"}]})
            return
        self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length:
            self.rfile.read(length)

        if self.path.rstrip("/") == "/v1/chat/completions":
            self._send_json(
                200,
                {
                    "id": f"chatcmpl-{uuid4().hex}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "gpt-4o",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": (
                                    "Take 7i, aim at the center, and hold a smooth fade. "
                                    "The wind makes it play a touch longer, so favor the safe target."
                                ),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                },
            )
            return

        if self.path.rstrip("/") == "/v1/audio/transcriptions":
            self._send_json(200, {"text": "155 out, pin back right, wind into us"})
            return

        if self.path.rstrip("/") == "/v1/audio/speech":
            self._send_bytes(200, "audio/mpeg", b"ID3\x04\x00\x00\x00\x00\x00\x15KindCaddy readiness audio")
            return

        self._send_json(404, {"error": {"message": "not found"}})


@contextlib.contextmanager
def fake_openai_server() -> Any:
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), FakeOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextlib.contextmanager
def backend_server(openai_base_url: str, work_dir: Path) -> Any:
    port = _free_port()
    env = os.environ.copy()
    env.update(
        {
            "KINDCADDY_DB_PATH": str(work_dir / "kindcaddy_readiness.db"),
            "KINDCADDY_LOG_DIR": str(work_dir / "logs"),
            "KINDCADDY_JWT_SECRET": READINESS_JWT_SECRET,
            "KINDCADDY_API_KEY": "readiness-api-key",
            "OPENAI_API_KEY": "readiness-openai-key",
            "OPENAI_BASE_URL": openai_base_url,
            "APPLE_BUNDLE_ID": "com.kindcaddy.app",
            "GOOGLE_CLIENT_ID": "readiness-google-client-id",
        }
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "kindcaddy.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_openapi(base_url, proc)
        global READINESS_DB_PATH
        READINESS_DB_PATH = Path(env["KINDCADDY_DB_PATH"])
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_openapi(base_url: str, proc: subprocess.Popen[str]) -> None:
    deadline = time.time() + 45
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"backend exited early: {output[-2000:]}")
        try:
            response = httpx.get(f"{base_url}/openapi.json", timeout=2)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"backend did not become ready: {last_error}")


def _profile(display_name: str = "Readiness Tester") -> dict[str, Any]:
    return {
        "name": display_name,
        "handicap": 15.0,
        "shot_shape": "fade",
        "handed": "right",
        "chat_style": "minimal",
        "model_selection": "gpt_wrapper",
        "target_score": 90,
        "clubs": {
            "Driver": {"carry": 230, "total": 245},
            "7i": {"carry": 155, "total": 165},
            "PW": {"carry": 120, "total": 127},
        },
        "tendencies": {
            "under_pressure": "pushes right",
            "back_nine": "loses 3 yards on irons",
            "wind": "",
            "general": "tends to miss right",
        },
        "physical": {
            "gender": "male",
            "age_group": "30s",
            "driver_clubhead_speed_mph": 95.0,
            "workout_frequency": "2x/week",
            "practice_frequency": "weekly",
        },
    }


def _expect(response: httpx.Response, status: int, label: str) -> None:
    if response.status_code != status:
        raise AssertionError(f"{label}: expected {status}, got {response.status_code}: {response.text[:300]}")


def _assert(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def _create_local_auth_headers(display_name: str) -> tuple[str, dict[str, str]]:
    if READINESS_DB_PATH is None:
        raise RuntimeError("readiness database path is not initialized")

    user_id = uuid4().hex
    email = f"readiness_{uuid4().hex[:10]}@kindcaddy.test"
    google_sub = f"readiness-google-{uuid4().hex}"
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    with sqlite3.connect(str(READINESS_DB_PATH), timeout=10) as conn:
        conn.execute(
            "INSERT INTO users (id, google_sub, email, display_name, provider, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'google', ?, ?)",
            (user_id, google_sub, email, display_name, now_iso, now_iso),
        )
        conn.commit()

    now = int(time.time())
    token = jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + 24 * 3600},
        READINESS_JWT_SECRET,
        algorithm="HS256",
    )
    return user_id, {"Authorization": f"Bearer {token}"}


def run_pytest() -> str:
    cmd = [sys.executable, "-m", "pytest", "tests/"]
    completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=120)
    if completed.returncode != 0:
        raise RuntimeError((completed.stdout + completed.stderr)[-4000:])
    return "python -m pytest tests/ passed"


def run_backend_smoke(base_url: str) -> str:
    with httpx.Client(base_url=base_url, timeout=20) as client:
        response = client.get("/openapi.json")
        _expect(response, 200, "OpenAPI")
        _assert("paths" in response.json(), "OpenAPI response missing paths")

        response = client.post("/session", json={"profile": _profile()})
        _expect(response, 401, "missing auth gate")

        response = client.get("/auth/me", headers={"Authorization": "Bearer invalid-token"})
        _expect(response, 401, "invalid bearer token")

        response = client.get("/auth/me", headers={"X-API-Key": "wrong-key"})
        _expect(response, 401, "invalid API key")

        response = client.post(
            "/estimate-distances",
            json={"gender": "male", "handicap": 15.0, "driver_speed_mph": 95.0},
        )
        _expect(response, 200, "public estimate-distances")

        response = client.post(
            "/auth/email/register",
            json={
                "email": "blocked_email_auth@kindcaddy.test",
                "password": "TestPass123!",
                "display_name": "Blocked Email Auth",
            },
        )
        _expect(response, 404, "removed email register route")

        user_id, headers = _create_local_auth_headers("Readiness Tester")
        _assert(bool(user_id), "local test user id is empty")
        client.headers.update(headers)

        response = client.get("/auth/me")
        _expect(response, 200, "auth/me with bearer")
        response = client.post("/session", json={"profile": _profile(), "model": "gpt-4o", "max_tokens": 512})
        _expect(response, 200, "create session")
        session = response.json()
        session_id = session["session_id"]
        _assert(bool(session_id), "session_id missing")
        _assert(bool(session.get("briefing")), "briefing missing")

        response = client.post("/session/recover", json={})
        _expect(response, 200, "recover active round")
        _assert(response.json()["session_id"] == session_id, "recover did not return current session")

        _play_full_round(client, session_id)

        response = client.post("/advice", json={"session_id": session_id, "text": "155 out, pin back right, wind into us"})
        _expect(response, 200, "advice")
        advice_text = response.json().get("text", "")
        _assert("7i" in advice_text or len(advice_text) > 20, "advice response was not substantive")

        response = client.get(f"/session/{session_id}")
        _expect(response, 200, "session state")
        state = response.json()
        round_id = state.get("round_id")
        _assert(bool(round_id), "round_id missing from session state")

        response = client.post(f"/rounds/{round_id}/finish", json={"status": "completed"})
        _assert(response.status_code in (200, 409), f"finish expected 200 or 409, got {response.status_code}")

        response = client.get("/rounds?limit=20")
        _expect(response, 200, "round list")
        rounds = response.json()["rounds"]
        finished = next((item for item in rounds if item["id"] == round_id), None)
        _assert(finished is not None, "finished round not present in /rounds")
        _assert(finished["holes_played"] == 18, "finished round does not show 18 holes")
        _assert(finished["total_strokes"] == sum(SCORES), "finished round total strokes mismatch")
        _assert(finished["summary_text"], "finished round summary_text missing")

        response = client.get(f"/rounds/{round_id}")
        _expect(response, 200, "round detail")
        detail = response.json()
        _assert(len(detail.get("scores", [])) == 18, "round detail missing score entries")
        _assert(len(detail.get("shots", [])) >= 10, "round detail missing shot entries")

        response = client.get("/rounds/stats")
        _expect(response, 200, "round stats")
        stats = response.json()
        _assert(stats["total_holes"] >= 18, "stats total_holes did not include completed round")
        _assert(stats["miss_tendencies"].get("right", 0) >= 6, "stats right miss tendency missing")

        response = client.get("/insights")
        _expect(response, 200, "insights")
        insights = response.json()
        _assert(insights.get("rounds_analyzed", 0) >= 1, "insights did not analyze completed round")

        response = client.get("/calibration")
        _expect(response, 200, "calibration")
        _assert(len(response.json().get("suggestions", [])) >= 1, "calibration suggestions missing")

        response = client.post("/tts", json={"text": "Nice swing."})
        _expect(response, 200, "tts")
        _assert(response.headers.get("content-type") == "audio/mpeg", "tts did not return audio/mpeg")
        _assert(len(response.content) > 10, "tts returned empty audio")

        response = client.post(
            "/transcribe",
            data={"session_id": session_id},
            files={"audio": ("sample.wav", b"RIFF\x24\x00\x00\x00WAVEfmt ", "audio/wav")},
        )
        _expect(response, 200, "transcribe")
        _assert(response.json().get("transcript"), "transcribe response missing transcript")

    return "auth, session, advice, round persistence, insights, calibration, TTS, and transcription passed"


def _play_full_round(client: httpx.Client, session_id: str) -> None:
    def command(name: str, args: str = "") -> None:
        response = client.post("/command", json={"session_id": session_id, "command": name, "args": args})
        _expect(response, 200, f"command {name}")

    command("newround")
    command("weather", "72F wind 8mph SW")
    seven_iron_by_hole = {
        1: ("7i", 145, "right"),
        2: ("7i", 150, "right"),
        3: ("7i", 148, "right"),
        4: ("7i", 152, None),
        5: ("7i", 146, "right"),
        6: ("7i", 150, None),
        7: ("7i", 148, "right"),
        8: ("7i", 145, "right"),
        9: ("7i", 150, None),
        10: ("7i", 147, "right"),
    }

    for hole, (score, par) in enumerate(zip(SCORES, DEFAULT_PARS), start=1):
        command("hole", str(hole))
        if hole in seven_iron_by_hole:
            club, distance, miss = seven_iron_by_hole[hole]
            command("shot", f"{club} {distance}" + (f" {miss}" if miss else ""))
        if par == 3 and hole not in seven_iron_by_hole:
            command("shot", "PW 118")
        command("score", str(score))

    command("summary")


def run_rate_limit_smoke(base_url: str) -> str:
    with httpx.Client(base_url=base_url, timeout=20) as client:
        _, headers = _create_local_auth_headers("Rate Limit Tester")
        client.headers.update(headers)

        statuses: list[int] = []
        for _ in range(12):
            response = client.post("/session", json={"profile": _profile("Rate Limit Tester"), "model": "gpt-4o"})
            statuses.append(response.status_code)
            if response.status_code == 429:
                break
        _assert(429 in statuses, f"session rate limit did not trigger, statuses={statuses}")
    return "session creation returned 429 under burst traffic"


def run_ios_static_checks() -> str:
    project_file = IOS_PROJECT / "project.pbxproj"
    info_plist = ROOT / "ios" / "KindCaddy" / "KindCaddy" / "Info.plist"
    config_swift = ROOT / "ios" / "KindCaddy" / "KindCaddy" / "Config.swift"
    for path in (project_file, info_plist, config_swift):
        if not path.exists():
            raise RuntimeError(f"missing {path.relative_to(ROOT)}")

    project_text = project_file.read_text(encoding="utf-8")
    info_text = info_plist.read_text(encoding="utf-8")
    config_text = config_swift.read_text(encoding="utf-8")
    checks = {
        "project references Info.plist": "INFOPLIST_FILE = KindCaddy/Info.plist" in project_text,
        "bundle id present": "PRODUCT_BUNDLE_IDENTIFIER = com.kindcaddy.app" in project_text,
        "backend URL Info.plist key present": "KindCaddyBackendURL" in info_text,
        "API key Info.plist key present": "KindCaddyAPIKey" in info_text,
        "localhost ATS exception scoped": "<key>localhost</key>" in info_text,
        "Config reads Info.plist": "Bundle.main.infoDictionary" in config_text,
        "Config does not hardcode backend URL": "https://" not in config_text and "http://" not in config_text,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError(", ".join(failed))
    return "Info.plist/config/project references are present and no backend URL is hardcoded in Config.swift"


def run_ios_build() -> str:
    if shutil.which("xcodebuild") is None:
        raise RuntimeError("xcodebuild not found")

    cmd = [
        "xcodebuild",
        "-project",
        str(IOS_PROJECT),
        "-scheme",
        IOS_SCHEME,
        "-quiet",
        "-configuration",
        "Debug",
        "-sdk",
        "iphonesimulator",
        "-destination",
        "generic/platform=iOS Simulator",
        "CODE_SIGNING_ALLOWED=NO",
        "KINDCADDY_BACKEND_URL=http://127.0.0.1:8765",
        "KINDCADDY_API_KEY=readiness-api-key",
        "GOOGLE_CLIENT_ID=readiness-google-client-id",
        "build",
    ]
    completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=300)
    if completed.returncode != 0:
        raise RuntimeError((completed.stdout + completed.stderr)[-6000:])
    return "xcodebuild Debug iphonesimulator succeeded"


def run_deployment_static_checks() -> str:
    install_sh = ROOT / "deploy" / "install.sh"
    service = ROOT / "deploy" / "kindcaddy.service"
    operations = ROOT / "deploy" / "OPERATIONS.md"
    for path in (install_sh, service, operations):
        if not path.exists():
            raise RuntimeError(f"missing {path.relative_to(ROOT)}")

    install_text = install_sh.read_text(encoding="utf-8")
    service_text = service.read_text(encoding="utf-8")
    checks = {
        "install uses requirements.txt": "pip install -r requirements.txt" in install_text,
        "service binds localhost": "--host 127.0.0.1" in service_text,
        "service uses EnvironmentFile": "EnvironmentFile=" in service_text,
        "service restarts automatically": "Restart=always" in service_text,
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError(", ".join(failed))
    return "deploy install/service static checks passed"


def add_manual_gates(runner: Runner) -> None:
    runner.manual("IR-1", "physical device Debug build", "Requires connected device and signing team.")
    runner.manual("IR-1", "Release archive validation", "Requires Xcode signing/archive workflow.")
    runner.manual("IR-3", "Apple and Google Sign-In provider flows", "Requires provider credentials and device/reviewer accounts.")
    runner.manual("IR-4", "on-device critical round UI flow", "Requires simulator/device interaction.")
    runner.manual("IR-5", "microphone permission and real TTS playback", "Requires simulator/device audio path.")
    runner.manual("DR-1/DR-2", "EC2 install, systemd, Caddy, HTTPS smoke", "Requires target EC2 host access.")
    runner.manual("DR-3", "production log review", "Requires deployed environment logs.")
    runner.manual("PL-1..PL-7", "public launch business/legal/store gates", "Requires policy, support, monitoring, and App Store Connect review.")
    runner.manual("Model Quality", "real GPT scenario benchmark", "Requires real OPENAI_API_KEY and human quality review.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-pytest", action="store_true", help="Skip python -m pytest tests/")
    parser.add_argument("--skip-ios-build", action="store_true", help="Skip xcodebuild simulator build")
    parser.add_argument("--skip-rate-limit", action="store_true", help="Skip burst rate-limit smoke")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runner = Runner()

    runner.run_step("Preflight", "repository root exists", lambda: str(ROOT))
    runner.run_step("DR-1", "deployment static checks", run_deployment_static_checks)
    runner.run_step("IR-2", "iOS static configuration checks", run_ios_static_checks)

    if args.skip_pytest:
        runner.manual("BR-2", "unit and deterministic tests", "Skipped by --skip-pytest.")
    else:
        runner.run_step("BR-2", "unit and deterministic tests", run_pytest)

    with tempfile.TemporaryDirectory(prefix="kindcaddy-readiness-") as temp_name:
        temp_dir = Path(temp_name)
        with fake_openai_server() as openai_base_url:
            with backend_server(openai_base_url, temp_dir) as base_url:
                runner.run_step("BR-1/BR-3/BR-4/BR-5", "backend no-secret smoke and full round flow", lambda: run_backend_smoke(base_url))
                if args.skip_rate_limit:
                    runner.manual("BR-6", "rate-limit smoke", "Skipped by --skip-rate-limit.")
                else:
                    runner.run_step("BR-6", "rate-limit smoke", lambda: run_rate_limit_smoke(base_url))

    if args.skip_ios_build:
        runner.manual("IR-1", "iOS simulator build", "Skipped by --skip-ios-build.")
    else:
        runner.run_step("IR-1", "iOS simulator build", run_ios_build)

    add_manual_gates(runner)
    return runner.print_summary()


if __name__ == "__main__":
    raise SystemExit(main())
