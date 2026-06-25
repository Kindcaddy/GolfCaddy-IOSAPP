"""KindCaddy CLI - interactive golf caddy powered by GPT-4o.

Usage:
    OPENAI_API_KEY=sk-... python -m kindcaddy [--profile PATH] [--model MODEL]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .agent.fatigue_model import FatigueModelTool
from .agent.score_calculator import ScoreCalculatorTool
from .agent.shot_tracker import ShotRecord, ShotTrackerTool
from .agent.weather_tool import WeatherTool
from .caddy import Caddy
from .profile import GolferProfile, load_profile

console = Console()

HELP_TEXT = """
[bold]KindCaddy Commands:[/bold]

  [cyan]/profile[/cyan]              View your golfer profile
  [cyan]/weather[/cyan] <conditions> Set weather (e.g., /weather 72F wind 12mph SW)
  [cyan]/altitude[/cyan] <feet>      Set course altitude
  [cyan]/newround[/cyan]             Start a new round
  [cyan]/hole[/cyan] <number>        Set current hole (1-18)
  [cyan]/score[/cyan] <strokes>      Log score for current hole
  [cyan]/shot[/cyan] <club> <dist> [miss] Log a shot (e.g., /shot 7i 150 right)
  [cyan]/scorecard[/cyan]            View scorecard
  [cyan]/summary[/cyan]              Get round analysis
  [cyan]/help[/cyan]                 Show this help
  [cyan]/quit[/cyan]                 Exit

  Anything else is a question for your caddy!
  Example: "162 out, pin back right, water right, good lie"
"""


def parse_weather_input(text: str) -> dict:
    """Parse natural weather input including voice speech patterns.

    Handles: '72F wind 12mph SW', '72 12 225', 'gusty wind 7 miles per hour',
    'wind blowing into me', 'left to right 10 mph', '7 mile/hour from behind'.
    """
    import re

    result = {
        "temp_f": 75.0,
        "wind_speed_mph": 0.0,
        "wind_deg": 0.0,
        "wind_gust_mph": 0.0,
        "humidity": 50,
        "description": "",
    }

    lower = text.lower()

    compass_to_deg = {
        "N": 0, "NE": 45, "E": 90, "SE": 135,
        "S": 180, "SW": 225, "W": 270, "NW": 315,
    }

    # Speed unit pattern — matches mph, mile/hour, miles per hour, miles an hour, kph
    _speed_unit = r"(?:mph|mile[s]?(?:\s*/\s*|\s+(?:per|an)\s+)hour|kph)"

    # Temperature
    temp_match = re.search(r"(\d+)\s*[Ff]", text)
    if temp_match:
        result["temp_f"] = float(temp_match.group(1))

    # Wind speed — "12 mph", "wind 12 mph", "12 miles per hour", "gusty 7 mph"
    wind_match = re.search(rf"(\d+)\s*{_speed_unit}", text, re.IGNORECASE)
    if wind_match:
        result["wind_speed_mph"] = float(wind_match.group(1))

    # Wind gust — explicit "gusts 15 mph" or "sometimes 15 mph"
    gust_match = re.search(rf"(?:gust[sy]?\s+|sometimes\s+)(\d+)\s*{_speed_unit}", text, re.IGNORECASE)
    if gust_match:
        result["wind_gust_mph"] = float(gust_match.group(1))
    elif re.search(r"\bgust[sy]\b", lower) and result["wind_speed_mph"] > 0:
        # "gusty wind 7 mph" — treat peak as ~40% above average
        result["wind_gust_mph"] = round(result["wind_speed_mph"] * 1.4, 1)

    # Wind direction — compass abbreviations
    for compass, deg in compass_to_deg.items():
        if re.search(rf"\b{compass}\b", text, re.IGNORECASE):
            result["wind_deg"] = deg
            break

    # Wind direction — degrees
    deg_match = re.search(r"(\d{2,3})\s*(?:deg|°)", text)
    if deg_match:
        result["wind_deg"] = float(deg_match.group(1))

    # Wind direction — natural golf phrases
    if not result["wind_deg"]:
        if re.search(r"left\s+to\s+right|from\s+(?:the\s+)?left", lower):
            result["wind_deg"] = 270   # wind from left → blowing right
        elif re.search(r"right\s+to\s+left|from\s+(?:the\s+)?right", lower):
            result["wind_deg"] = 90    # wind from right → blowing left
        elif re.search(r"into\s+(?:me|us)|head\s*wind|against", lower):
            result["wind_deg"] = 180   # headwind (approximation)
        elif re.search(r"from\s+behind|helping|down\s*wind|tail\s*wind", lower):
            result["wind_deg"] = 0     # tailwind (approximation)

    # Humidity
    hum_match = re.search(r"(\d+)\s*%\s*(?:humidity)?", text)
    if hum_match:
        result["humidity"] = int(hum_match.group(1))

    # Description keywords
    desc_map = {
        "gusty": "gusty", "windy": "windy", "breezy": "breezy",
        "rain": "rain", "cloudy": "cloudy", "overcast": "overcast",
        "sunny": "clear", "clear": "clear", "foggy": "foggy",
        "drizzle": "drizzle", "misty": "misty", "overcast": "overcast",
    }
    for keyword, label in desc_map.items():
        if keyword in lower:
            result["description"] = label
            break

    return result


def run_cli(
    profile_path: str,
    model: str = "gpt-4o",
    api_key: str | None = None,
    max_tokens: int = 1024,
    history_len: int = 20,
) -> None:
    """Main CLI loop."""
    path = Path(profile_path)
    if not path.exists():
        console.print(f"[red]Profile not found: {path}[/red]")
        console.print("Create a profile JSON file or use profiles/example.json")
        return

    profile = load_profile(path)
    console.print(
        Panel(
            f"[bold green]Welcome to KindCaddy![/bold green]\n\n"
            f"Golfer: [bold]{profile.name}[/bold] (handicap {profile.handicap})\n"
            f"Model: [dim]{model}[/dim] | Max tokens: [dim]{max_tokens}[/dim]\n\n"
            f"Type [cyan]/help[/cyan] for commands or just ask about your shot.",
            title="KindCaddy",
            border_style="green",
        )
    )

    caddy = Caddy(
        model=model, api_key=api_key,
        max_tokens=max_tokens, history_len=history_len,
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

    while True:
        try:
            user_input = console.input("\n[bold cyan]You:[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]See you on the course![/dim]")
            break

        if not user_input:
            continue

        # Handle commands
        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            command = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if command == "/quit" or command == "/exit":
                console.print("[dim]See you on the course![/dim]")
                break

            elif command == "/help":
                console.print(HELP_TEXT)

            elif command == "/profile":
                _show_profile(profile)

            elif command == "/weather":
                if not args:
                    console.print(f"[dim]{caddy.round_state.get_conditions_summary()}[/dim]")
                else:
                    weather_data = parse_weather_input(args)
                    weather_tool.set_weather_manual(**weather_data)
                    caddy.round_state.update_weather(**weather_data)
                    console.print(
                        f"[green]Weather set:[/green] {caddy.round_state.get_conditions_summary()}"
                    )

            elif command == "/altitude":
                try:
                    alt = float(args)
                    caddy.round_state.set_altitude(alt)
                    console.print(f"[green]Altitude set to {alt:.0f}ft[/green]")
                except ValueError:
                    console.print("[red]Usage: /altitude <feet>[/red]")

            elif command == "/newround":
                caddy.round_state.start_round(profile)
                caddy.agent.reset_for_new_round()
                console.print(
                    Panel(
                        f"[bold green]New round started![/bold green]\n"
                        f"Hole 1 | Target: {profile.target_score or 'not set'}\n"
                        f"Set weather with [cyan]/weather[/cyan] and let's go!",
                        border_style="green",
                    )
                )

            elif command == "/hole":
                try:
                    hole = int(args)
                    caddy.round_state.set_hole(hole)
                    console.print(f"[green]Now on hole {hole}[/green]")
                    _run_agent_and_alert(caddy, "hole_change")
                except ValueError:
                    console.print("[red]Usage: /hole <number>[/red]")

            elif command == "/score":
                try:
                    strokes = int(args)
                    hole = caddy.round_state.current_hole or 1
                    score_calc.log_score(hole, strokes)
                    par = score_calc.pars[hole - 1]
                    diff = strokes - par
                    label = {-2: "Eagle!", -1: "Birdie!", 0: "Par", 1: "Bogey", 2: "Double bogey"}.get(
                        diff, f"+{diff}" if diff > 0 else str(diff)
                    )
                    console.print(f"[green]Hole {hole}: {strokes} ({label})[/green]")

                    # Auto-advance to next hole
                    if hole < 18:
                        caddy.round_state.set_hole(hole + 1)
                        console.print(f"[dim]Moving to hole {hole + 1}[/dim]")

                    _run_agent_and_alert(caddy, "score_logged")
                except ValueError:
                    console.print("[red]Usage: /score <strokes>[/red]")

            elif command == "/shot":
                _handle_shot_log(args, shot_tracker, caddy)

            elif command == "/scorecard":
                console.print(Panel(score_calc.get_scorecard(), title="Scorecard", border_style="blue"))

            elif command == "/summary":
                console.print("\n[bold yellow]Caddy:[/bold yellow] ", end="")
                score_data = score_calc.execute({})
                shot_data = shot_tracker.get_round_summary()
                for text in caddy.generate_summary(str(score_data), str(shot_data)):
                    console.print(text, end="")
                console.print()

            else:
                console.print(f"[red]Unknown command: {command}. Type /help for commands.[/red]")

            continue

        # Regular caddy question -- get advice
        console.print("\n[bold yellow]Caddy:[/bold yellow] ", end="")

        # Run agent triggers first
        alerts = caddy.run_agent_triggers("interaction")
        if alerts:
            # Let the caddy weave alerts into the response naturally
            caddy.agent._pending_alerts.extend(alerts)

        for text in caddy.get_advice(user_input):
            console.print(text, end="")
        console.print()


def _run_agent_and_alert(caddy: Caddy, trigger_type: str) -> None:
    """Run agent triggers and display any proactive alerts."""
    alerts = caddy.run_agent_triggers(trigger_type)
    if alerts:
        console.print("\n[bold yellow]Caddy:[/bold yellow] ", end="")
        for text in caddy.generate_proactive_message(alerts):
            console.print(text, end="")
        console.print()


def _handle_shot_log(args: str, shot_tracker: ShotTrackerTool, caddy: Caddy) -> None:
    """Parse and log a shot: /shot 7i 150 right"""
    parts = args.split()
    if len(parts) < 1:
        console.print("[red]Usage: /shot <club> [actual_distance] [miss_direction][/red]")
        console.print("[dim]Example: /shot 7i 150 right[/dim]")
        return

    club = parts[0]
    actual_dist = None
    miss_dir = None

    if len(parts) >= 2:
        try:
            actual_dist = float(parts[1])
        except ValueError:
            miss_dir = parts[1]

    if len(parts) >= 3:
        miss_dir = parts[2]

    # Look up expected carry from profile for pattern detection
    profile_carry = None
    if caddy.round_state.profile and club in caddy.round_state.profile.clubs:
        profile_carry = caddy.round_state.profile.clubs[club].carry

    shot = ShotRecord(
        hole=caddy.round_state.current_hole or 1,
        club=club,
        actual_distance=actual_dist,
        miss_direction=miss_dir,
        profile_carry=profile_carry,
    )
    shot_tracker.log_shot(shot)

    msg = f"[green]Shot logged: {club}"
    if actual_dist:
        msg += f" - {actual_dist:.0f}yd"
    if miss_dir:
        msg += f" (missed {miss_dir})"
    msg += "[/green]"
    console.print(msg)

    _run_agent_and_alert(caddy, "shot_logged")


def _show_profile(profile: GolferProfile) -> None:
    """Display the golfer profile in a nice table."""
    table = Table(title=f"{profile.name}'s Bag", border_style="blue")
    table.add_column("Club", style="cyan")
    table.add_column("Carry (yd)", justify="right")
    table.add_column("Total (yd)", justify="right")

    for club, dist in profile.clubs.items():
        table.add_row(club, str(dist.carry), str(dist.total))

    console.print(table)
    console.print(f"[dim]Handicap: {profile.handicap} | Shape: {profile.shot_shape} | {profile.handed}-handed[/dim]")
    if profile.tendencies.under_pressure:
        console.print(f"[dim]Under pressure: {profile.tendencies.under_pressure}[/dim]")
    if profile.tendencies.back_nine:
        console.print(f"[dim]Back 9: {profile.tendencies.back_nine}[/dim]")


def main():
    import os

    parser = argparse.ArgumentParser(description="KindCaddy - AI Golf Caddy")
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
        "--max-tokens",
        type=int,
        default=1024,
        help="Max response tokens (default: 1024)",
    )
    parser.add_argument(
        "--history-len",
        type=int,
        default=10,
        help="Number of conversation turns to keep (default: 10)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        console.print("[red]Set the OPENAI_API_KEY environment variable.[/red]")
        return

    run_cli(
        args.profile, args.model, api_key=api_key,
        max_tokens=args.max_tokens, history_len=args.history_len,
    )


if __name__ == "__main__":
    main()
