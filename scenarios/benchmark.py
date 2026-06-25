"""Benchmark script - runs test scenarios through the model and outputs results.

Usage:
    OPENAI_API_KEY=sk-... python scenarios/benchmark.py [--model MODEL] [--profile PROFILE]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from kindcaddy.agent.fatigue_model import FatigueModelTool
from kindcaddy.agent.score_calculator import ScoreCalculatorTool
from kindcaddy.agent.shot_tracker import ShotTrackerTool
from kindcaddy.agent.weather_tool import WeatherTool
from kindcaddy.caddy import Caddy
from kindcaddy.profile import load_profile

console = Console()


def run_scenario(caddy: Caddy, scenario: dict) -> dict:
    """Run a single test scenario and return results."""
    # Set weather
    weather = scenario.get("weather", {})
    weather_tool = caddy.agent.get_tool("weather")
    if weather_tool and weather:
        weather_tool.set_weather_manual(**weather)
        caddy.round_state.update_weather(**weather)

    # Set altitude
    if "altitude_ft" in scenario:
        caddy.round_state.set_altitude(scenario["altitude_ft"])

    # Set hole
    if "hole" in scenario:
        caddy.round_state.set_hole(scenario["hole"])
        caddy.round_state.is_active = True

    # Run agent triggers
    caddy.run_agent_triggers("interaction")

    # Get advice
    start_time = time.time()
    response_parts = []
    for text in caddy.get_advice(scenario["input"]):
        response_parts.append(text)
    elapsed = time.time() - start_time

    response = "".join(response_parts)

    return {
        "scenario_id": scenario["id"],
        "name": scenario["name"],
        "input": scenario["input"],
        "response": response,
        "elapsed_seconds": elapsed,
        "expected_club": scenario.get("expected_club", ""),
        "tests": scenario.get("tests", []),
    }


def main():
    import os

    parser = argparse.ArgumentParser(description="KindCaddy Benchmark")
    parser.add_argument(
        "--model",
        default="gpt-4o",
        help="OpenAI model name (default: gpt-4o)",
    )
    parser.add_argument(
        "--profile",
        default="profiles/example.json",
        help="Golfer profile to use",
    )
    parser.add_argument(
        "--scenarios",
        default="scenarios/test_scenarios.json",
        help="Path to scenarios file",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Save results to JSON file",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENAI_API_KEY"),
        help="OpenAI API key (default: OPENAI_API_KEY env)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=1024,
        help="Max response tokens (default: 1024)",
    )
    args = parser.parse_args()

    # Load scenarios
    scenarios_path = Path(args.scenarios)
    if not scenarios_path.exists():
        console.print(f"[red]Scenarios file not found: {scenarios_path}[/red]")
        return

    with open(scenarios_path) as f:
        scenarios = json.load(f)

    # Load profile
    profile_path = Path(args.profile)
    if not profile_path.exists():
        console.print(f"[red]Profile not found: {profile_path}[/red]")
        return

    profile = load_profile(profile_path)

    console.print(
        Panel(
            f"[bold]KindCaddy Benchmark[/bold]\n\n"
            f"Model: {args.model}\n"
            f"Profile: {profile.name} (handicap {profile.handicap})\n"
            f"Scenarios: {len(scenarios)}",
            border_style="blue",
        )
    )

    caddy = Caddy(model=args.model, api_key=args.api_key, max_tokens=args.max_tokens)
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

    results = []

    for i, scenario in enumerate(scenarios):
        console.print(f"\n[bold cyan]--- Scenario {i+1}/{len(scenarios)}: {scenario['name']} ---[/bold cyan]")
        console.print(f"[dim]Input: {scenario['input']}[/dim]")

        # Reset all state between scenarios so conditions don't leak
        caddy.round_state.conversation = []
        caddy.round_state.is_active = True
        caddy.round_state.altitude_ft = 0.0
        caddy.round_state.temp_f = 75.0
        caddy.round_state.wind_speed_mph = 0.0
        caddy.round_state.wind_deg = 0.0
        caddy.round_state.wind_gust_mph = 0.0
        caddy.round_state.humidity = 50
        caddy.round_state.weather_description = ""
        caddy.agent.reset_for_new_round()

        result = run_scenario(caddy, scenario)
        results.append(result)

        console.print(f"\n[yellow]Response ({result['elapsed_seconds']:.1f}s):[/yellow]")
        console.print(Panel(result["response"][:800], border_style="green"))

        if result["expected_club"]:
            console.print(f"[dim]Expected club: {result['expected_club']}[/dim]")

        console.print(f"[dim]Quality checks: {', '.join(result['tests'])}[/dim]")

    # Summary table
    console.print("\n")
    table = Table(title="Benchmark Results", border_style="blue")
    table.add_column("Scenario", style="cyan")
    table.add_column("Time (s)", justify="right")
    table.add_column("Expected Club")
    table.add_column("Response Length", justify="right")

    total_time = 0
    for r in results:
        total_time += r["elapsed_seconds"]
        table.add_row(
            r["name"],
            f"{r['elapsed_seconds']:.1f}",
            r["expected_club"],
            str(len(r["response"])),
        )

    console.print(table)
    console.print(f"\n[bold]Total time: {total_time:.1f}s | Average: {total_time/len(results):.1f}s per scenario[/bold]")

    # Save results if requested
    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        console.print(f"\n[green]Results saved to {output_path}[/green]")


if __name__ == "__main__":
    main()
