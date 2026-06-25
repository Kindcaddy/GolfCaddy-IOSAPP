"""Core caddy: context builder and model interface.

Connects the golfer's input, profile, weather, round state, and agent alerts
into a structured prompt, sends it to the LLM, and returns the response.

Uses the OpenAI Python SDK to call GPT-4o (or any OpenAI-compatible model).
"""

from __future__ import annotations

import os
from typing import Generator

from openai import OpenAI

from .agent.base import Alert, CaddyAgent
from .prompts import (
    build_alert_prompt,
    build_briefing_prompt,
    build_recap_prompt,
    build_summary_prompt,
    build_system_prompt,
)
from .round_state import RoundState
from .shot_planner import build_shot_plan


def _call_openai(
    client: OpenAI,
    model: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 1024,
) -> str:
    """Non-streaming chat completion via the OpenAI SDK."""
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def _summarize_insights(insights: dict | None) -> str:
    """Convert raw insights dict to a brief human-readable summary for prompts."""
    if not insights or insights.get("rounds_analyzed", 0) == 0:
        return "No previous rounds."

    parts: list[str] = []
    rounds = insights.get("rounds_analyzed", 0)
    parts.append(f"{rounds} rounds in history.")

    for club, data in insights.get("club_actuals", {}).items():
        delta = data.get("delta")
        if delta is not None and abs(delta) >= 5:
            direction = "shorter" if delta < 0 else "longer"
            parts.append(f"{club} carrying {abs(delta):.0f}yd {direction} than profile.")

    miss = insights.get("miss_tendencies") or {}
    total_misses = sum(miss.values())
    if total_misses >= 10:
        for direction, count in miss.items():
            if count / total_misses >= 0.60:
                parts.append(f"Dominant miss: {direction}.")
                break

    sp = insights.get("scoring_patterns") or {}
    front_avg = sp.get("front9_avg")
    back_avg = sp.get("back9_avg")
    if front_avg is not None and back_avg is not None and abs(back_avg - front_avg) >= 2:
        worse = "back 9" if back_avg > front_avg else "front 9"
        parts.append(f"Scores higher on {worse}.")

    trend = insights.get("improvement_trend")
    if trend:
        parts.append(f"Trend: {trend}.")

    return " ".join(parts)


def generate_pre_round_briefing(
    name: str,
    insights_summary: str,
    todos_text: str,
    last_recap: str,
    chat_style: str = "casual",
) -> str:
    """Generate a personalized pre-round briefing using GPT-4o-mini.

    Returns a briefing string, or a plain default greeting on any failure.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    client = OpenAI(api_key=api_key)

    prompt = build_briefing_prompt(
        name=name,
        chat_style=chat_style,
        insights_summary=insights_summary or "No previous rounds.",
        todos_text=todos_text or "No specific focus set.",
        last_recap=last_recap or "No previous round data.",
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Generate the pre-round briefing."},
    ]

    try:
        return _call_openai(client, "gpt-4o-mini", messages, max_tokens=150)
    except Exception:
        return f"Good to see you, {name}. Let's go play some good golf today."


STYLE_DISTILL_INTERVAL_ROUNDS = 3
STYLE_DISTILL_MIN_PAIRS = 6
STYLE_DISTILL_MAX_PAIRS = 24


def distill_user_style(user_id: str) -> str | None:
    """Distill a 1-2 sentence "preferred voice" descriptor from recent Q/A pairs.

    Reads up to ``STYLE_DISTILL_MAX_PAIRS`` recent (user, assistant) exchanges
    from the user's completed rounds and asks GPT-4o-mini to summarize how the
    caddy should talk to *this* golfer (length, tone, what to lead with). The
    descriptor is upserted into ``user_style_profile`` and returned. Any
    failure returns ``None`` and leaves the existing descriptor untouched.
    """
    from .db import (
        count_completed_rounds,
        get_assistant_reply_samples,
        upsert_style_profile,
    )

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    samples = get_assistant_reply_samples(user_id, limit=STYLE_DISTILL_MAX_PAIRS)
    if len(samples) < STYLE_DISTILL_MIN_PAIRS:
        return None

    transcript_lines: list[str] = []
    for s in samples:
        u = " ".join((s.get("user_text") or "").split())[:240]
        a = " ".join((s.get("assistant_text") or "").split())[:360]
        if not u or not a:
            continue
        transcript_lines.append(f"Golfer: {u}\nCaddy: {a}")
    if not transcript_lines:
        return None

    transcript = "\n\n".join(transcript_lines)
    system_prompt = (
        "You are analyzing how a golf caddy should communicate with a SPECIFIC "
        "golfer based on their past chat. Read the transcripts and write 1-2 "
        "concise sentences (max 50 words total) describing the golfer's preferred "
        "voice for the caddy: response length, level of detail, tone, and what to "
        "lead with (e.g. club + aim point, swing thought, banter). Write in the "
        "form of a directive to the caddy, e.g. 'Lead with the club and a single "
        "aim point. Keep replies under 3 sentences. Avoid swing-thought lectures.' "
        "Do NOT include the golfer's name, scores, or any quoted text."
    )

    client = OpenAI(api_key=api_key)
    try:
        descriptor = _call_openai(
            client,
            "gpt-4o-mini",
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Recent transcripts:\n\n{transcript}"},
            ],
            max_tokens=120,
            temperature=0.3,
        ).strip()
    except Exception:
        return None

    if not descriptor:
        return None

    try:
        rounds_done = count_completed_rounds(user_id)
        upsert_style_profile(user_id, descriptor, rounds_done)
    except Exception:
        return None
    return descriptor


def maybe_distill_user_style(user_id: str) -> str | None:
    """Run :func:`distill_user_style` only every N completed rounds.

    Acts as a cheap rate-limiter so we don't burn an LLM call after every single
    round. Returns the new descriptor when distillation actually ran, ``None``
    otherwise.
    """
    from .db import count_completed_rounds, get_style_profile

    rounds_done = count_completed_rounds(user_id)
    if rounds_done < STYLE_DISTILL_INTERVAL_ROUNDS:
        return None

    existing = get_style_profile(user_id)
    last_at = int((existing or {}).get("rounds_at_distill", 0) or 0)
    if rounds_done - last_at < STYLE_DISTILL_INTERVAL_ROUNDS:
        return None
    return distill_user_style(user_id)


def generate_recap_from_data(
    profile_snapshot: dict,
    scores: list[dict],
    shots: list[dict],
    chat_style: str = "casual",
) -> str:
    """Generate a post-round recap without an active session.

    Uses GPT-4o-mini for cost efficiency — recap generation doesn't require
    the full model.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    client = OpenAI(api_key=api_key)

    # Build a readable profile summary from the snapshot dict
    name = profile_snapshot.get("name", "Golfer")
    profile_lines = [f"Name: {name}"]
    handicap = profile_snapshot.get("handicap")
    if handicap is not None:
        profile_lines.append(f"Handicap: {handicap}")
    shot_shape = profile_snapshot.get("shot_shape")
    if shot_shape:
        profile_lines.append(f"Shot shape: {shot_shape}")
    clubs = profile_snapshot.get("clubs") or {}
    if clubs:
        club_str = ", ".join(
            f"{k}: {v.get('carry', '?')}yd" for k, v in clubs.items()
        )
        profile_lines.append(f"Clubs: {club_str}")
    profile_summary = "\n".join(profile_lines)

    # Build round data summary
    total_strokes = sum(s.get("strokes", 0) for s in scores)
    total_par = sum(s.get("par", 0) for s in scores)
    holes_played = len(scores)
    vs_par = total_strokes - total_par
    round_data = (
        f"Holes played: {holes_played}\n"
        f"Total strokes: {total_strokes}\n"
        f"Total par: {total_par}\n"
        f"Score vs par: {vs_par:+d}"
    )
    if scores:
        score_lines = "\n".join(
            f"  Hole {s['hole']}: {s['strokes']} (par {s['par']})" for s in scores
        )
        round_data += f"\nHole-by-hole:\n{score_lines}"

    # Build shot history summary
    if shots:
        shot_lines = []
        for s in shots:
            line = f"  Hole {s['hole']}: {s['club']}"
            if s.get("actual_distance"):
                line += f" {int(s['actual_distance'])}yd"
            if s.get("miss_direction"):
                line += f" miss-{s['miss_direction']}"
            shot_lines.append(line)
        shot_history = "\n".join(shot_lines)
    else:
        shot_history = "No shot data recorded."

    prompt = build_recap_prompt(
        profile_summary=profile_summary,
        chat_style=chat_style,
        round_data=round_data,
        shot_history=shot_history,
    )

    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Generate the post-round recap."},
    ]

    return _call_openai(client, "gpt-4o-mini", messages, max_tokens=512)


class Caddy:
    """The core caddy engine -- builds context, calls the model, manages the agent."""

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 1024,
        history_len: int = 20,
        user_insights: dict | None = None,
        user_notes: list[dict] | None = None,
        user_style: str | None = None,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.history_len = history_len
        self.user_insights = user_insights
        self.user_notes = user_notes or []
        self.user_style = (user_style or "").strip() or None
        self.round_state = RoundState()
        self.agent = CaddyAgent()
        self._client = OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url,
        )
        self.last_user_embedding: list[float] | None = None
        self.last_embed_model: str | None = None
        self.last_memory_hits: list[dict] = []

    def get_advice(self, user_input: str) -> Generator[str, None, None]:
        """Get caddy advice for a shot question. Yields the full response."""
        memory_tool = self.agent.get_tool("memory")
        self.last_memory_hits = []
        self.last_user_embedding = None
        self.last_embed_model = None
        if memory_tool is not None:
            try:
                result = memory_tool.execute({"user_text": user_input})
                self.last_memory_hits = result.get("hits", []) or []
            except Exception:
                self.last_memory_hits = []
            try:
                vec = memory_tool.embed_text(user_input)
                if vec:
                    self.last_user_embedding = vec
                    self.last_embed_model = getattr(memory_tool, "embed_model", None)
            except Exception:
                self.last_user_embedding = None

        system_prompt = self._build_system_prompt()

        alerts = self.agent.get_pending_alerts()
        enhanced_input = user_input

        # Prepend a weather notice when conditions are missing so the model
        # cannot silently skip asking — it must surface the gap to the golfer.
        rs = self.round_state
        conditions_missing = not rs.weather_received
        if conditions_missing:
            enhanced_input = (
                "[No weather data available yet. Before giving a full club recommendation, "
                "briefly note you don't have current conditions and ask the golfer about wind "
                "and temperature. Then give your best estimate based on the profile.]\n\n"
                + enhanced_input
            )

        if alerts:
            alert_text = "\n".join(
                f"[Agent Alert - {a.source}]: {a.message}" for a in alerts
            )
            enhanced_input = (
                f"{enhanced_input}\n\n"
                f"[Your agent tools detected the following -- weave this into your response naturally]\n"
                f"{alert_text}"
            )

        # Deterministic shot plan v1:
        # If we can parse a usable yardage, compute club + plays-like distance in Python.
        # The model should narrate this plan, not recalculate it.
        plan = None
        if self.round_state.profile:
            fatigue_adj = 0.0
            fatigue_tool = self.agent.get_tool("fatigue")
            if fatigue_tool:
                fatigue_data = fatigue_tool.execute({})
                fatigue_adj = max(
                    0.0, float(fatigue_data.get("distance_adjustment_yards", 0) or 0)
                )

            plan = build_shot_plan(
                user_text=user_input,
                round_state=self.round_state,
                profile=self.round_state.profile,
                user_insights=self.user_insights,
                fatigue_adjustment_yards=fatigue_adj,
            )

        if plan:
            adjustment_breakdown = ", ".join(
                f"{k} {v:+.1f}yd"
                for k, v in plan.applied_adjustments.items()
                if abs(v) >= 0.1
            ) or "none"
            enhanced_input = (
                f"{enhanced_input}\n\n"
                "[Deterministic Shot Plan - use exactly this calculation and club]\n"
                f"raw_distance_yards={plan.raw_distance_yards}\n"
                f"plays_like_yards={plan.plays_like_yards}\n"
                f"recommended_club={plan.recommended_club}\n"
                f"club_effective_carry={plan.recommended_club_carry}\n"
                f"lie={plan.lie}\n"
                f"wind_kind={plan.wind_kind}\n"
                f"adjustments={adjustment_breakdown}\n"
                "Instructions: Do NOT recalculate club distance. Keep the exact club and "
                "plays-like distance above, then provide aim and miss strategy in your "
                "normal caddy voice."
            )

        self.round_state.add_message("user", user_input)

        messages = [{"role": "system", "content": system_prompt}]

        recent = self.round_state.conversation[-self.history_len:]
        for msg in recent[:-1]:
            messages.append(msg)
        messages.append({"role": "user", "content": enhanced_input})

        try:
            response = _call_openai(
                self._client, self.model, messages,
                max_tokens=self.max_tokens,
            )
            self.round_state.add_message("assistant", response)
            yield response

        except Exception:
            error_msg = "Unable to reach the caddy right now. Please try again."
            yield error_msg
            self.round_state.add_message("assistant", error_msg)

    def generate_proactive_message(self, alerts: list[Alert]) -> Generator[str, None, None]:
        """Generate a proactive message from agent alerts."""
        if not alerts:
            return

        alert_text = "\n".join(
            f"- [{a.source}] (priority: {a.priority}): {a.message}" for a in alerts
        )

        prompt = build_alert_prompt(
            profile_summary=self.round_state.get_profile_summary(),
            chat_style=self.round_state.profile.chat_style if self.round_state.profile else "casual",
            alerts=alert_text,
            round_state=self.round_state.get_round_state_summary(),
        )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Generate a brief proactive alert for the golfer."},
        ]

        try:
            response = _call_openai(
                self._client, self.model, messages,
                max_tokens=min(1024, self.max_tokens),
            )
            self.round_state.add_message("assistant", "[Caddy Alert] " + response)
            yield response

        except Exception as e:
            yield f"(Alert generation failed: {e})"

    def generate_summary(self, score_data: str, shot_data: str) -> Generator[str, None, None]:
        """Generate a round summary."""
        prompt = build_summary_prompt(
            profile_summary=self.round_state.get_profile_summary(),
            chat_style=self.round_state.profile.chat_style if self.round_state.profile else "casual",
            round_data=score_data,
            shot_history=shot_data,
        )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Generate a round summary and analysis."},
        ]

        try:
            response = _call_openai(
                self._client, self.model, messages,
                max_tokens=self.max_tokens,
            )
            yield response

        except Exception as e:
            yield f"Error generating summary: {e}"

    def run_agent_triggers(self, trigger_type: str = "interaction") -> list[Alert]:
        """Run the agent trigger loop and return any alerts."""
        return self.agent.on_trigger(self.round_state, trigger_type)

    def _build_system_prompt(self) -> str:
        """Build the full system prompt with current context."""
        extra_context = []

        fatigue_tool = self.agent.get_tool("fatigue")
        if fatigue_tool:
            adj = fatigue_tool.execute({}).get("distance_adjustment_yards", 0)
            if abs(adj) > 0:
                extra_context.append(
                    f"Fatigue: the golfer is hitting {abs(adj):.0f} yards SHORTER than normal due to "
                    f"back-9 fatigue. Club UP to compensate (e.g., if the shot is 155yd, treat it as "
                    f"{155 + abs(adj):.0f}yd when picking a club)."
                )

        tracker = self.agent.get_tool("shot_tracker")
        if tracker:
            adj = tracker.get_distance_adjustment()
            if abs(adj) > 0:
                direction = "short" if adj > 0 else "long"
                extra_context.append(
                    f"Performance pattern: golfer hitting {abs(adj):.0f} yards {direction} today. "
                    f"Adjust club selection accordingly."
                )

        round_summary = self.round_state.get_round_state_summary()
        if extra_context:
            round_summary += "\n\nAgent observations:\n" + "\n".join(extra_context)

        score_tool = self.agent.get_tool("score_calculator")
        if score_tool:
            score_data = score_tool.execute({})
            if score_data.get("holes_played", 0) > 0:
                round_summary += (
                    f"\n\nScore: {score_data['total_strokes']} through "
                    f"{score_data['holes_played']} holes ({score_data['vs_par']:+d})"
                )

        if self.user_insights:
            memory_parts = []

            # Club carry differences of 5+ yards vs profile
            club_notes = []
            for club, data in self.user_insights.get("club_actuals", {}).items():
                delta = data.get("delta")
                if delta is not None and abs(delta) >= 5:
                    direction = "shorter" if delta < 0 else "longer"
                    club_notes.append(
                        f"{club} carrying {abs(delta):.0f}yd {direction} than profile"
                    )
            if club_notes:
                memory_parts.append("Actual carry data: " + "; ".join(club_notes) + ".")

            # Dominant miss if one direction has 60%+ of tracked misses
            miss = self.user_insights.get("miss_tendencies", {})
            total_misses = sum(miss.values())
            if total_misses >= 10:
                for direction, count in miss.items():
                    if count / total_misses >= 0.60:
                        memory_parts.append(
                            f"Dominant miss: {direction} on {count}/{total_misses} tracked shots."
                        )
                        break

            # Front 9 vs back 9 avg strokes/hole, flag if delta >= 2
            sp = self.user_insights.get("scoring_patterns") or {}
            front_avg = sp.get("front9_avg")
            back_avg = sp.get("back9_avg")
            if front_avg is not None and back_avg is not None:
                nine_delta = back_avg - front_avg
                if abs(nine_delta) >= 2:
                    worse_half = "back 9" if nine_delta > 0 else "front 9"
                    memory_parts.append(
                        f"Averages {abs(nine_delta):.1f} strokes/hole higher on the {worse_half}."
                    )

            # Pressure pattern on holes 15-18
            pressure_delta = self.user_insights.get("pressure_scoring_delta")
            if pressure_delta is not None and abs(pressure_delta) >= 0.3:
                direction = "worse" if pressure_delta > 0 else "better"
                memory_parts.append(
                    f"Holes 15-18 average {abs(pressure_delta):.1f} strokes/hole {direction} than holes 1-14."
                )

            # Improvement trend
            trend = self.user_insights.get("improvement_trend")
            rounds_n = self.user_insights.get("rounds_analyzed", 0)
            if trend and rounds_n >= 4:
                memory_parts.append(f"Scoring trend over {rounds_n} rounds: {trend}.")

            if memory_parts:
                round_summary += "\n\n## Golfer Memory\n" + " ".join(memory_parts)

        # Inject free-form golfer reminders and swing thoughts
        if self.user_notes:
            notes_text = "\n".join(f"- {n['note_text']}" for n in self.user_notes)
            round_summary += (
                "\n\n## Golfer Reminders (things the golfer asked you to remember)\n"
                + notes_text
                + "\nWeave these naturally into your advice when relevant — "
                "especially when they describe a mental tendency or commitment issue."
            )

        memory_tool = self.agent.get_tool("memory")
        if memory_tool is not None and self.last_memory_hits:
            try:
                memory_block = memory_tool.render_prompt_block(self.last_memory_hits)
            except Exception:
                memory_block = ""
            if memory_block:
                round_summary += (
                    "\n\n## Past Similar Advice (from previous rounds)\n"
                    + memory_block
                    + "\nUse these only if they're directly relevant. Briefly reference "
                    "what worked or didn't last time — don't repeat verbatim."
                )

        if self.user_style:
            round_summary += (
                "\n\n## This Golfer's Preferred Voice\n"
                + self.user_style
                + "\nFollow this voice unless it conflicts with safety or correctness."
            )

        return build_system_prompt(
            profile_summary=self.round_state.get_profile_summary(),
            chat_style=self.round_state.profile.chat_style if self.round_state.profile else "casual",
            conditions=self.round_state.get_conditions_summary(),
            round_state=round_summary,
        )
