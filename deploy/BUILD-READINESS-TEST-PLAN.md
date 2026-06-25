# KindCaddy Build and Public Launch Readiness Test Plan

Use this checklist before TestFlight beta, app store submission, public launch, production backend updates, or any release that changes backend, iOS, auth, prompts, model behavior, deployment, or persistence.

This plan intentionally does not inspect `.env` or `ios/KindCaddy/Secrets.xcconfig`. Validate secret-dependent behavior through process startup, API responses, iOS build settings, and runtime smoke tests only.

## Release Gates

A build is ready when all required gates pass:

- Local backend unit tests pass.
- The API imports and starts with release-like environment variables.
- Auth-protected endpoints reject unauthenticated requests in production-like mode.
- A full session can start, receive advice, log round state, finish a round, and recover or summarize as expected.
- The iOS app builds for simulator and device/archive with release configuration.
- The iOS app talks to the intended HTTPS backend and completes the critical round flow.
- EC2/systemd/Caddy checks pass for deployed builds.
- Logs show no repeated P0/P1 failures during smoke testing.
- Rollback instructions and previous known-good backend/app build are available.

A public launch is ready when the build gates pass and these additional launch gates pass:

- Privacy policy, terms, App Store privacy answers, and user data deletion process are ready.
- Production auth, rate limits, dependency posture, and secret rotation process have been reviewed.
- Uptime monitoring, alerting, OpenAI cost/quota monitoring, and incident response workflow are active.
- Public support channels, FAQ, triage ownership, and response expectations are in place.
- App Store listing, screenshots, review notes, compliance notes, and public release metadata are complete.
- At least one production-like load/cost/reliability pass has completed without launch-blocking issues.

## Scope

### In Scope

- Python package import, dependency install, and pytest suite.
- FastAPI startup, OpenAPI availability, auth, session, advice, commands, voice, rounds, insights, notes, calibration, TTS, and rate limit behavior.
- SQLite initialization and round persistence.
- iOS build, signing, Info.plist-driven config, network client behavior, auth flows, round/advice UI, speech, TTS playback, feedback, and resume behavior.
- EC2 deployment, systemd service health, Caddy HTTPS, security group posture, and log review.
- GPT/OpenAI-dependent smoke tests and scenario benchmark when keys are available.
- Public launch readiness: privacy, compliance, support, observability, capacity, cost controls, App Store metadata, and go/no-go decisioning.

### Out of Scope

- Reading, printing, copying, or committing `.env`.
- Reading, printing, copying, or committing `ios/KindCaddy/Secrets.xcconfig`.
- Manual inspection of secret values. Confirm configuration by observing successful startup, build, and authenticated behavior.

## Test Matrix

| Area | Required For | Owner | Pass Signal |
|---|---|---|---|
| Backend unit tests | Every backend release | Engineering | `python -m pytest tests/` passes |
| Backend E2E | Beta/prod release | Engineering | Round flow script completes against live server |
| API auth/security | Beta/prod release | Engineering | Unauthorized requests fail; authorized requests pass |
| iOS simulator build | Every iOS release | Engineering | Debug build installs and launches |
| iOS device/archive build | TestFlight/App Store | Engineering | Release archive succeeds |
| iOS critical user flow | Every iOS release | Product/QA | Auth -> start round -> first advice works |
| Deployment smoke | Every backend deploy | Engineering | HTTPS `/docs` and session smoke pass |
| Observability/triage | Beta/prod release | Support/QA | Logs and feedback funnel are usable |
| Privacy/compliance | Public launch | Product/Legal/Engineering | Policies, disclosures, and deletion path are ready |
| Security/abuse | Public launch | Engineering | Auth, dependency, rate-limit, and secret practices reviewed |
| Scale/cost | Public launch | Engineering/Product | Load and OpenAI cost checks are acceptable |
| App Store listing | Public launch | Product | Metadata, screenshots, review notes, and compliance answers are complete |
| Support readiness | Public launch | Support/Product | Support channel, FAQ, and triage ownership are active |

## Preflight

1. Confirm the working tree only contains intended release changes.
2. Confirm `requirements.txt` installs cleanly in a fresh virtual environment.
3. Confirm the backend starts from a clean database path.
4. Confirm no ignored secret files are staged.
5. Confirm the target release version/build number is set for iOS and backend release notes.
6. Confirm test devices and simulator versions cover the minimum supported iOS version.

Suggested local commands:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/
```

## Backend Readiness

### BR-1: Import and Startup

Steps:

1. Start the API with a temporary database path and release-like environment variables.
2. Confirm import does not fail.
3. Confirm `/docs` or `/openapi.json` returns successfully.

Pass criteria:

- Server starts without import errors.
- Database initializes automatically.
- OpenAPI route responds.

Notes:

- `KINDCADDY_JWT_SECRET` is required for production-like auth startup.
- Do not inspect `.env`; inject variables through the shell/session used for the test.

### BR-2: Unit and Deterministic Tests

Steps:

1. Run `python -m pytest tests/`.
2. If failures occur, rerun the specific failing test file after fixing the issue.

Pass criteria:

- `tests/test_memory.py` passes.
- `tests/test_shot_planner.py` passes.
- No warnings indicate skipped critical coverage.

### BR-3: API Auth Gate

Steps:

1. Start a production-like API instance with auth configured.
2. Request a protected endpoint without `Authorization` or `X-API-Key`.
3. Request the same endpoint with valid credentials.
4. Try an invalid bearer token or API key.

Pass criteria:

- Missing/invalid credentials receive an auth failure.
- Valid credentials can create sessions and call app endpoints.
- Public endpoints remain intentionally public.

### BR-4: Session and Round Flow

Steps:

1. Create a session with a valid golfer profile.
2. Start or recover a round.
3. Send commands for hole, weather, score, and shot logging.
4. Ask for advice.
5. Finish the round.
6. Confirm round data is persisted and visible through round/history endpoints.

Pass criteria:

- Session ID is returned.
- Round state changes match commands.
- Advice response is useful and non-empty.
- Finished round writes scores, shots, and profile snapshot to SQLite.
- Recover behavior is clear after restart or TTL expiration.

### BR-5: LLM, Voice, and External Services

Steps:

1. Send a normal advice request.
2. Exercise course name extraction if the release touches round setup.
3. Test transcription with a short audio file when voice surfaces changed.
4. Test TTS generation and playback endpoint when voice output changed.
5. Confirm failures from OpenAI are surfaced to the client with actionable errors.

Pass criteria:

- Advice, transcription, and TTS succeed when dependencies are available.
- Timeout/error paths do not crash the server.
- Logs contain request context without secret leakage.

### BR-6: Rate Limits and Error Handling

Steps:

1. Exercise repeated calls to advice, TTS, transcription, recap, and session creation.
2. Confirm rate-limited responses are clear.
3. Confirm the iOS app displays retryable errors appropriately.

Pass criteria:

- Hot endpoints return `429` when limits are exceeded.
- Normal user pacing is not blocked.
- Error responses do not expose implementation details or secrets.

## iOS Readiness

### IR-1: Project Build

Steps:

1. Open `ios/KindCaddy/KindCaddy.xcodeproj`.
2. Build Debug for simulator.
3. Build Debug for a physical device.
4. Archive Release for TestFlight/App Store when applicable.

Pass criteria:

- Build succeeds with no signing, Info.plist, package, or compiler errors.
- App launches without config fatal errors.
- Release archive validates in Xcode Organizer.

### IR-2: Runtime Configuration

Steps:

1. Launch the app on simulator and device.
2. Confirm the app targets the intended backend host through runtime behavior.
3. Confirm HTTPS is used for deployed backend testing.
4. Confirm missing or malformed config fails loudly during internal testing.

Pass criteria:

- API calls go to the expected backend.
- App does not require hardcoded secrets in Swift files.
- Localhost HTTP is only used for local simulator development.

### IR-3: Auth Flow

Steps:

1. Test email sign-up/sign-in if enabled for the build.
2. Test Apple Sign-In on a physical device.
3. Test Google Sign-In when configured for the release.
4. Kill and relaunch the app to confirm token/session persistence behavior.
5. Sign out and confirm protected screens are no longer accessible.

Pass criteria:

- Successful auth stores a usable token.
- Failed auth shows a clear error.
- Token is attached as bearer auth for protected requests.
- Sign-out clears local auth state.

### IR-4: Critical Round Flow

Steps:

1. Complete onboarding/profile setup.
2. Start a round.
3. Ask the first advice question.
4. Change hole and weather.
5. Log a shot and score.
6. Continue the round after app background/foreground.
7. Finish the round and view recap/history if available.

Pass criteria:

- No crash or blocking spinner.
- Round state shown in the UI matches backend state.
- Advice is readable, timely, and relevant.
- Finish-round behavior preserves data.

### IR-5: Voice and Audio

Steps:

1. Grant and deny microphone/speech permissions.
2. Use push-to-talk for a short golf question.
3. Verify transcript quality is acceptable.
4. Verify TTS playback starts, stops, and recovers from interruption.
5. Test on device, not only simulator.

Pass criteria:

- Permission denial has a usable fallback.
- Voice request reaches the backend.
- TTS does not block future advice requests.
- Audio interruptions do not break the round flow.

### IR-6: UX, Accessibility, and Reliability

Steps:

1. Test light/dark mode if supported.
2. Test small and large Dynamic Type sizes.
3. Test poor network or airplane mode recovery.
4. Test app relaunch while a round is active.
5. Submit in-app feedback from a failed and successful state.

Pass criteria:

- Critical controls remain visible and tappable.
- Offline/errors provide clear recovery.
- Feedback includes enough context for beta triage.

## Deployment Readiness

### DR-1: Package and Install

Steps:

1. Package the backend according to `deploy/EC2-DEPLOY.md` or `deploy/OPERATIONS.md`.
2. Install on EC2 using `deploy/install.sh`.
3. Confirm dependencies match the actual app requirements.

Pass criteria:

- Fresh install completes.
- `kindcaddy.service` starts.
- No missing Python dependencies in service logs.

### DR-2: Service and HTTPS Smoke

Steps:

1. Check `systemctl status kindcaddy`.
2. Check `systemctl status caddy`.
3. Confirm port 8000 is not publicly exposed.
4. Confirm HTTPS `/docs` or `/openapi.json` works through Caddy.
5. Run a quick authenticated session test against the deployed URL.

Pass criteria:

- `kindcaddy` and `caddy` are active.
- Public traffic goes through HTTPS.
- API responds through the production hostname.
- Session smoke test succeeds.

### DR-3: Logs and Monitoring

Steps:

1. Watch `journalctl -u kindcaddy -f` during smoke tests.
2. Review `advice_logs_*.jsonl` and `kpi_events_*.jsonl` after beta-path testing.
3. Confirm errors include enough identifiers for triage.
4. Confirm logs do not include secrets, bearer tokens, or raw config files.

Pass criteria:

- No repeated startup, auth, OpenAI, database, or network errors.
- Critical funnel events are visible: auth, round start, first advice, resume, finish round.
- P0/P1 events are understood before release.

## Public Launch Readiness

These gates are required before opening the app to public users beyond a controlled beta.

### PL-1: Privacy, Terms, and User Data

Steps:

1. Confirm public privacy policy and terms are published and reachable from the App Store listing or app as required.
2. Confirm App Store privacy nutrition labels match actual data collection and third-party processing.
3. Confirm user data deletion and account deletion process works or has a documented manual support path.
4. Confirm feedback, logs, and support payloads avoid unnecessary personal data and never include secrets.
5. Confirm any AI/GPT disclosure language accurately explains that advice is generated and may be imperfect.

Pass criteria:

- Privacy policy and terms are live.
- App Store privacy answers match the app and backend behavior.
- Users have a clear path to delete account/data.
- Public copy does not overpromise golf outcomes or model accuracy.

### PL-2: Security and Abuse Readiness

Steps:

1. Confirm production backend requires valid auth or API key for protected endpoints.
2. Confirm public port 8000 is closed and public traffic uses HTTPS through Caddy.
3. Confirm rate limits protect advice, transcription, TTS, recap, auth, and session creation.
4. Run a dependency review for Python and iOS dependencies.
5. Confirm a secret rotation plan exists for OpenAI, JWT, API key, OAuth, and APNs credentials.
6. Confirm logs, crash reports, screenshots, and support templates do not expose tokens or secret values.

Pass criteria:

- No known launch-blocking auth bypass or exposed admin/dev surface.
- Dependency risks are accepted or fixed.
- Credential rotation can be performed without rebuilding the whole app where possible.
- Abuse scenarios have reasonable throttling or mitigation.

### PL-3: Reliability, Capacity, and Cost

Steps:

1. Run a small production-like load test against staging or the production candidate.
2. Test concurrent users starting sessions and asking for first advice.
3. Estimate OpenAI cost for expected launch-day usage, including advice, transcription, and TTS.
4. Confirm OpenAI quota and billing limits can support the launch plan.
5. Confirm backend disk/database growth expectations for rounds, logs, and audio-related workflows.
6. Confirm restart behavior, session loss behavior, and user-facing recovery are acceptable.

Pass criteria:

- Expected launch traffic does not produce unacceptable latency or error rates.
- Cost and quota risks are understood before public traffic starts.
- Storage/log growth has a cleanup or monitoring plan.
- Backend restart does not create an unexplained public user failure mode.

### PL-4: Monitoring and Incident Response

Steps:

1. Confirm someone is assigned to watch logs during launch.
2. Confirm alerting or manual checks cover backend availability, high 5xx rate, auth failures, OpenAI failures, and Caddy/TLS failures.
3. Confirm support intake captures user ID, session ID, round ID, device, iOS version, local time, and expected vs actual behavior.
4. Confirm P0/P1 severity rules and escalation owner are known.
5. Confirm rollback steps are rehearsed or documented for backend and iOS release issues.

Pass criteria:

- Launch has a named on-call or owner.
- Incident response can start within the expected support window.
- Rollback/hotfix decision criteria are explicit.
- Support can connect user reports to backend logs.

### PL-5: App Store and Public Listing

Steps:

1. Confirm app name, subtitle, description, keywords, screenshots, preview, category, age rating, and support URL.
2. Confirm Apple Sign-In and Google Sign-In configuration matches the public bundle/app identifiers.
3. Confirm review notes explain demo credentials or test path if Apple review needs them.
4. Confirm public screenshots do not show secrets, private endpoints, internal logs, or private user data.
5. Confirm version and build numbers match release notes.

Pass criteria:

- App Store Connect metadata is complete.
- Reviewer can exercise the core flow.
- Public listing accurately represents current functionality.
- Compliance answers are consistent with actual behavior.

### PL-6: Public Support and Operations

Steps:

1. Confirm support email/inbox, feedback route, or website contact path is monitored.
2. Prepare FAQ or canned responses for auth issues, backend outages, bad advice, billing/cost notices if applicable, and account deletion.
3. Confirm beta triage labels apply to public support.
4. Define public launch success metrics: activation, round start, first advice, resume success, finish round, crash-free sessions, and support volume.
5. Define stop-the-line thresholds, such as repeated auth lockouts, round data loss, or resume failure above an agreed limit.

Pass criteria:

- Public users have a visible support path.
- Support has enough context to respond without engineering for every report.
- Launch metrics are reviewed daily during the first public release window.
- New user acquisition can be paused if P0/P1 issues exceed thresholds.

### PL-7: Legal, Safety, and Product Claims

Steps:

1. Review public copy for claims about scoring improvement, professional advice, handicap changes, and AI accuracy.
2. Confirm the app frames golf advice as informational and user-controlled.
3. Confirm weather, distance, and club recommendations include enough context to avoid misleading certainty.
4. Confirm any user-generated data or training-data use is disclosed consistently with policy.

Pass criteria:

- Public copy is accurate and conservative.
- AI limitations are clear enough for public users.
- Product claims match what the app can reliably deliver today.

## End-to-End Release Scenario

Run this scenario before any beta/prod release:

1. Deploy backend to staging or production-like EC2.
2. Confirm HTTPS smoke succeeds.
3. Install the iOS build on a physical device.
4. Sign in.
5. Start a round with a realistic profile.
6. Ask: "155 out, pin back right, wind into us."
7. Confirm advice includes club/target/shot-shape reasoning.
8. Log a shot and score.
9. Background the app for at least two minutes, then resume.
10. Finish the round.
11. Confirm history/insights/calibration behavior after finish.
12. Review logs and beta triage fields.

Pass criteria:

- The user can complete the full flow without developer intervention.
- Backend and iOS agree on state.
- Advice quality is acceptable for the intended release audience.
- Logs support support/debug workflows.

## Model Quality Check

Run scenario benchmarking when prompt, model, agent tools, profile context, or advice behavior changes:

```bash
OPENAI_API_KEY=sk-... python scenarios/benchmark.py --model gpt-4o
```

Pass criteria:

- No obvious regression in club selection, wind handling, altitude handling, lie handling, or tone.
- Any changed advice behavior is intentional and captured in release notes.

## Regression Checklist

Before signing off, verify:

- Existing users can authenticate.
- New users can authenticate.
- Sessions work after app relaunch.
- Session loss after backend restart has a clear user path.
- Round finish persists scores and shots.
- Insights/calibration still compute after finished rounds.
- Advice still includes golfer profile context.
- Weather updates influence advice.
- Fatigue and agent observations do not create contradictory advice.
- Errors are understandable on iOS.
- No secret values are printed in logs, screenshots, crash reports, or support payloads.

## Release Sign-Off Template

```text
Release:
Backend commit:
iOS build:
Environment:
Tester:
Date:

Backend unit tests: PASS/FAIL
Backend E2E: PASS/FAIL
API auth gate: PASS/FAIL
iOS simulator build: PASS/FAIL
iOS device/archive build: PASS/FAIL
Critical round flow: PASS/FAIL
Voice/TTS: PASS/FAIL/NOT TESTED
Deployment smoke: PASS/FAIL
Log review: PASS/FAIL
Privacy/compliance: PASS/FAIL/NOT PUBLIC
Security/abuse review: PASS/FAIL/NOT PUBLIC
Scale/cost review: PASS/FAIL/NOT PUBLIC
Monitoring/incident response: PASS/FAIL/NOT PUBLIC
App Store listing: PASS/FAIL/NOT PUBLIC
Support readiness: PASS/FAIL/NOT PUBLIC
Known issues:
Rollback target:
Decision: SHIP BETA/SHIP PUBLIC/HOLD
```

