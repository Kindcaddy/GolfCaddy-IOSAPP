"""KindaWu -- voice interface for KindCaddy using OpenAI Whisper and TTS.

Speak your shot questions and hear the caddy's advice. Uses OpenAI for
speech-to-text (Whisper), text-to-speech, and the caddy brain (GPT-4o).

Usage:
    OPENAI_API_KEY=sk-... python -m kindcaddy.voice_app --profile profiles/example.json

Commands can be spoken, e.g. "new round", "hole 7", "weather 72 wind 10", or
ask a shot question: "162 out, pin back right, water right".
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from .agent.fatigue_model import FatigueModelTool
from .agent.score_calculator import ScoreCalculatorTool
from .agent.shot_tracker import ShotRecord, ShotTrackerTool
from .agent.weather_tool import WeatherTool
from .caddy import Caddy
from .profile import GolferProfile, load_profile
from .main import (
    parse_weather_input,
    _handle_shot_log,
    _run_agent_and_alert,
)

console = Console()

# ---------------------------------------------------------------------------
# Audio: record (WAV) and play (TTS MP3)
# ---------------------------------------------------------------------------


def record_wav(seconds: float = 5.0, sample_rate: int = 16000) -> bytes:
    """Record from microphone and return WAV file bytes (16-bit mono)."""
    import sounddevice as sd
    import soundfile as sf
    import numpy as np

    console.print("[dim]Listening... (speak now)[/dim]")
    frames = int(seconds * sample_rate)
    block = sd.rec(frames, samplerate=sample_rate, channels=1, dtype=np.int16)
    sd.wait()
    buffer = bytes()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, block, sample_rate, subtype="PCM_16")
        f.flush()
        buffer = Path(f.name).read_bytes()
        Path(f.name).unlink(missing_ok=True)
    return buffer


def transcribe_with_whisper(api_key: str, wav_bytes: bytes) -> str:
    """Transcribe WAV audio to text using OpenAI Whisper."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav_bytes)
        f.flush()
        try:
            with open(f.name, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text",
                )
            return (transcript or "").strip()
        finally:
            Path(f.name).unlink(missing_ok=True)


def speak_with_tts(api_key: str, text: str, voice: str = "alloy") -> None:
    """Synthesize text to speech with OpenAI TTS and play on this machine."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
    )
    audio_bytes = response.content
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio_bytes)
        f.flush()
        path = f.name
    try:
        if sys.platform == "darwin":
            subprocess.run(["afplay", path], check=True, capture_output=True)
        elif sys.platform == "win32":
            os.startfile(path)  # opens default player; file left for player to read
            return  # skip unlink so playback can finish
        else:
            # Linux: try mpv or ffplay
            for cmd in (["mpv", "--no-terminal", path], ["ffplay", "-nodisp", "-autoexit", path]):
                r = subprocess.run(cmd, capture_output=True)
                if r.returncode == 0:
                    break
            else:
                console.print("[dim]TTS saved; install mpv or ffmpeg to play automatically.[/dim]")
    finally:
        if sys.platform != "win32":
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Voice command handling (mirror main.py commands for voice)
# ---------------------------------------------------------------------------


def run_voice_loop(
    profile_path: str,
    model: str,
    openai_api_key: str,
    record_seconds: float = 6.0,
    tts_voice: str = "alloy",
    max_tokens: int = 1024,
    history_len: int = 10,
) -> None:
    """Main voice loop: record -> transcribe -> handle -> respond -> speak."""
    path = Path(profile_path)
    if not path.exists():
        console.print(f"[red]Profile not found: {path}[/red]")
        return

    profile = load_profile(path)
    caddy = Caddy(
        model=model,
        api_key=openai_api_key,
        max_tokens=max_tokens,
        history_len=history_len,
    )
    caddy.round_state.profile = profile
    caddy.round_state.target_score = profile.target_score

    weather_tool = WeatherTool()
    shot_tracker = ShotTrackerTool()
    score_calc = ScoreCalculatorTool()
    fatigue_tool = FatigueModelTool()
    caddy.agent.register_tool(weather_tool)
    caddy.agent.register_tool(shot_tracker)
    caddy.agent.register_tool(score_calc)
    caddy.agent.register_tool(fatigue_tool)

    console.print(
        Panel(
            f"[bold green]KindaWu[/bold green] – voice caddy\n\n"
            f"Golfer: [bold]{profile.name}[/bold]\n"
            f"Say your command or shot question after the beep. "
            f"Record length: [dim]{record_seconds}s[/dim] | TTS voice: [dim]{tts_voice}[/dim]\n\n"
            f"[dim]Say 'quit' or press Ctrl+C to exit.[/dim]",
            title="KindaWu",
            border_style="green",
        )
    )

    while True:
        try:
            # Optional: short beep to cue user (skip if no simple beep available)
            console.print("\n[bold cyan]▶[/bold cyan] Recording...")
            wav_bytes = record_wav(seconds=record_seconds)
            if len(wav_bytes) < 1000:
                console.print("[yellow]No audio captured; try again.[/yellow]")
                continue

            text = transcribe_with_whisper(openai_api_key, wav_bytes)
            if not text:
                console.print("[yellow]Nothing heard; try again.[/yellow]")
                continue

            console.print(f"[bold]You said:[/bold] {text}")

            # Normalize: treat "new round" like /newround, "hole 7" like /hole 7
            lower = text.strip().lower()
            if lower in ("quit", "exit", "goodbye", "stop"):
                speak_with_tts(openai_api_key, "See you on the course.", voice=tts_voice)
                console.print("[dim]See you on the course.[/dim]")
                break

            # Dispatch voice-style commands (no leading slash)
            parts = text.strip().split(maxsplit=1)
            first = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if first == "new" and "round" in lower:
                caddy.round_state.start_round(profile)
                caddy.agent.reset_for_new_round()
                msg = f"New round started. Hole 1. Target {profile.target_score or 'not set'}."
                speak_with_tts(openai_api_key, msg, voice=tts_voice)
                console.print(f"[green]{msg}[/green]")
                continue

            if first == "hole" and args.isdigit():
                hole = int(args)
                caddy.round_state.set_hole(hole)
                _run_agent_and_alert(caddy, "hole_change")
                msg = f"Now on hole {hole}."
                speak_with_tts(openai_api_key, msg, voice=tts_voice)
                console.print(f"[green]{msg}[/green]")
                continue

            if first == "weather" and args:
                weather_data = parse_weather_input(args)
                weather_tool.set_weather_manual(**weather_data)
                caddy.round_state.update_weather(**weather_data)
                msg = f"Weather set. {caddy.round_state.get_conditions_summary()}"
                speak_with_tts(openai_api_key, msg, voice=tts_voice)
                console.print(f"[green]{msg}[/green]")
                continue

            if first == "score" and args.isdigit():
                strokes = int(args)
                hole = caddy.round_state.current_hole or 1
                score_calc.log_score(hole, strokes)
                par = score_calc.pars[hole - 1]
                diff = strokes - par
                label = {-2: "Eagle", -1: "Birdie", 0: "Par", 1: "Bogey", 2: "Double bogey"}.get(
                    diff, f"plus {diff}" if diff > 0 else str(diff)
                )
                msg = f"Hole {hole}: {strokes}, {label}."
                if hole < 18:
                    caddy.round_state.set_hole(hole + 1)
                    msg += f" Moving to hole {hole + 1}."
                speak_with_tts(openai_api_key, msg, voice=tts_voice)
                _run_agent_and_alert(caddy, "score_logged")
                console.print(f"[green]{msg}[/green]")
                continue

            if first == "shot" and args:
                _handle_shot_log(args, shot_tracker, caddy)
                speak_with_tts(openai_api_key, "Shot logged.", voice=tts_voice)
                continue

            if "scorecard" in lower:
                card = score_calc.get_scorecard()
                speak_with_tts(openai_api_key, "Showing scorecard on screen.", voice=tts_voice)
                console.print(Panel(card, title="Scorecard", border_style="blue"))
                continue

            if "summary" in lower:
                score_data = score_calc.execute({})
                shot_data = shot_tracker.get_round_summary()
                full: list[str] = []
                for t in caddy.generate_summary(str(score_data), str(shot_data)):
                    full.append(t)
                reply = "".join(full)
                console.print(f"[bold yellow]Caddy:[/bold yellow] {reply}")
                speak_with_tts(openai_api_key, reply, voice=tts_voice)
                continue

            # Default: treat as shot question → get advice and speak it
            alerts = caddy.run_agent_triggers("interaction")
            if alerts:
                caddy.agent._pending_alerts.extend(alerts)

            full_reply: list[str] = []
            for chunk in caddy.get_advice(text):
                full_reply.append(chunk)
            reply = "".join(full_reply)

            console.print(f"[bold yellow]Caddy:[/bold yellow] {reply}")
            speak_with_tts(openai_api_key, reply, voice=tts_voice)

        except KeyboardInterrupt:
            console.print("\n[dim]See you on the course.[/dim]")
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            speak_with_tts(openai_api_key, "Something went wrong. Please try again.", voice=tts_voice)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="KindaWu -- voice interface for KindCaddy (OpenAI Whisper + TTS + GPT-4o)"
    )
    parser.add_argument(
        "--profile",
        default="profiles/example.json",
        help="Path to golfer profile JSON",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o",
        help="OpenAI model name (default: gpt-4o)",
    )
    parser.add_argument(
        "--openai-api-key",
        default=os.environ.get("OPENAI_API_KEY"),
        help="OpenAI API key (default: OPENAI_API_KEY env)",
    )
    parser.add_argument(
        "--record-seconds",
        type=float,
        default=6.0,
        help="Recording length in seconds (default: 6)",
    )
    parser.add_argument(
        "--tts-voice",
        default="alloy",
        choices=["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
        help="OpenAI TTS voice (default: alloy)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Max response tokens",
    )
    parser.add_argument(
        "--history-len",
        type=int,
        default=10,
        help="Conversation history length",
    )
    args = parser.parse_args()

    if not args.openai_api_key:
        console.print("[red]Set OPENAI_API_KEY (env or --openai-api-key) for Whisper and TTS.[/red]")
        sys.exit(1)

    run_voice_loop(
        profile_path=args.profile,
        model=args.model,
        openai_api_key=args.openai_api_key,
        record_seconds=args.record_seconds,
        tts_voice=args.tts_voice,
        max_tokens=args.max_tokens,
        history_len=args.history_len,
    )


if __name__ == "__main__":
    main()
