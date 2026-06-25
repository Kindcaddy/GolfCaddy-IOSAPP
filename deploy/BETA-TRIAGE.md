# KindCaddy Beta Support Triage

Use this runbook during external beta to keep issue response consistent and actionable.

## Intake Channels

- In-app feedback (`Report Issue / Send Feedback`) emails support.
- Direct tester messages (TestFlight notes, Slack, SMS) should be copied into the same tracker.

## Required Intake Fields

- Reporter name and contact.
- User ID (if available).
- Session ID and round ID (included by in-app feedback template).
- Device + iOS version.
- Time of issue and local timezone.
- One-sentence expected vs actual behavior.

## Triage Labels

- `P0`: data loss, auth lockout, crash on critical flow, cannot start/resume round.
- `P1`: degraded but usable (retries needed, occasional API failure, broken UI state).
- `P2`: cosmetic or low-impact friction.

## 15-Minute Triage Loop

1. Confirm reproducibility from logs (`kpi_events_*.jsonl`, `advice_logs_*.jsonl`).
2. Assign severity and owner.
3. Reply to tester with status and workaround if available.
4. File/refresh a ticket with IDs and reproduction.
5. Mark whether fix ships as hotfix, next patch, or backlog.

## Daily Beta Review

- Activation funnel: auth success -> round start -> first advice.
- Round durability funnel: continue round tap -> recovery success/fail.
- Reliability funnel: error surfaced -> retry success.
- Feedback funnel: feedback taps and top issue themes.

## Escalation

- Any repeated `P0` in the same day escalates to immediate release gate review.
- If resume failures exceed 5% of continue attempts, pause new tester invites until fixed.
