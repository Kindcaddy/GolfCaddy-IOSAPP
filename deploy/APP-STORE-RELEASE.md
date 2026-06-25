# KindCaddy App Store Release Runbook(Only if you want to Commercialize it, otherwise, testflight is pretty good)

End-to-end guide for shipping KindCaddy from a fresh Xcode build all the way to the public App Store, including the TestFlight beta path along the way.

> **Mental model:** every build artifact is the same. It can flow down two independent paths in App Store Connect:
> - **TestFlight** path → external beta testers (light review, ~24h)
> - **Distribution** path → public App Store (full review, ~24–48h)
>
> You can iterate on TestFlight builds for weeks while the Distribution page sits half-finished. The two paths only converge if/when you decide to release publicly.

```text
Xcode Archive → Upload to App Store Connect
       │
       ▼
   Build appears in App Store Connect (after ~10–30 min processing)
       │
       ├──► TestFlight tab ──► External Group ──► Beta Review (~24h) ──► testers install
       │
       └──► Distribution tab ──► Attach to v1.0 ──► App Review (~24–48h) ──► public release
```

---

## Phase 0: One-time setup (do this first, in parallel with everything)

These are the slowest items. Start them now because they gate the App Store launch.

### Apple Developer + App Store Connect

- [ ] Apple Developer Program membership active ($99/yr)
- [ ] App record created in App Store Connect with bundle ID `com.kindcaddy.app`
- [ ] Xcode signing configured with the right team and bundle ID

### Business setup (gates IAP entirely)

App Store Connect → **Business**:

- [ ] Paid Apps Agreement → **Active**
- [ ] Bank account → linked + verified (Apple sends a small test deposit; ~1–3 days)
- [ ] Tax forms (W-9 US or W-8BEN non-US) → **Active**
- [ ] All three contacts assigned: Legal, Financial, Technical

> Until **all four** items above are Active, `Product.products(for:)` returns empty and the paywall shows "Subscription options are unavailable" everywhere — TestFlight, sandbox, and production. There is no workaround.

### Public legal pages (required at submission)

Both must be hosted on a public URL:

- [ ] Privacy Policy → e.g., `https://kindcaddy.app/privacy`
- [ ] Terms of Use / EULA → e.g., `https://kindcaddy.app/terms`
  - Must disclose: subscription title, length, price per period, free-trial length, auto-renewal terms, cancellation policy, payment to Apple ID
  - You can use [Apple's standard EULA](https://www.apple.com/legal/internet-services/itunes/dev/stdeula/) verbatim if you don't want to write your own

### Subscriptions in App Store Connect

App Store Connect → your app → **Monetization → Subscriptions**:

- [ ] Subscription group created (e.g., "KindCaddy Pro")
- [ ] `kindcaddy.pro.monthly` configured with price tier, localized name, description
- [ ] `kindcaddy.pro.yearly` configured
- [ ] Each subscription has a **Review Screenshot** (1242×2688 or similar) showing the paywall — required by App Review
- [ ] Status: **Ready to Submit** (will become "Waiting for Review" once attached to a version)

---

## Phase 1: Beta path (TestFlight)

This is what you do **first** to get builds in front of testers. No subscription setup, screenshots, or privacy labels required here.

### 1.1 Build and upload

```bash
# In Xcode:
# 1. Increment build number in Target → General → Build
# 2. Product → Archive
# 3. Distribute App → App Store Connect → Upload
```

- [ ] Archive succeeds without warnings
- [ ] Build appears in App Store Connect after processing email (~10–30 min)
- [ ] Build shows no compliance issues in the Builds list

### 1.2 Test Information (one-time per app)

App Store Connect → **TestFlight → Test Information** (left sidebar):

- [ ] Beta App Description filled in (see template at bottom of this doc)
- [ ] Feedback email
- [ ] Privacy Policy URL (required)
- [ ] Marketing URL (optional)
- [ ] Sign-In Information → demo email + password (required since app gates everything behind sign-in)
  - Demo account should have ~999 trial rounds remaining and exist on prod backend

### 1.3 Internal testing (no review, instant)

Use this for yourself and teammates with App Store Connect access.

- [ ] Internal Testing → "beta tester" group → add up to 100 Apple IDs from your team
- [ ] Attach the build → testers receive email + can install via TestFlight app immediately
- [ ] **Smoke test**: install on a real iPhone, sign in with demo account, start a round, ask for advice via mic, log a shot. Must work end-to-end.

### 1.4 External testing (Beta Review required, ~24h)

Use this for real-world testers via public link.

- [ ] External Testing → "beta tester" group exists
- [ ] **Manage** Public Link → raise tester limit from default 10 → up to 10,000
- [ ] Builds tab → **+** → pick your processed build
- [ ] Fill in "What to Test" notes for this build
- [ ] Submit for **Beta App Review**
- [ ] Status changes to **Waiting for Beta App Review** → **In Beta App Review** → **Approved**
- [ ] Once approved, share `https://testflight.apple.com/join/<code>` with testers

> **Subsequent builds** in the same version usually skip Beta Review — only the first build per version goes through. Significant changes (new features, different UI) may trigger another review.

### 1.5 Common Beta Review rejections (and how to avoid)

| Rejection reason | Fix |
|---|---|
| App crashes on launch | Test on a real device with fresh install before submitting |
| Reviewer can't sign in | Provide working demo credentials in Test Information |
| Missing Privacy Policy URL | Fill it in Test Information |
| Generic permission strings | `NSMicrophoneUsageDescription` should explain *why*, not "needs microphone" |
| Placeholder content visible | Remove all "Lorem ipsum", "TODO", debug labels |

> Missing subscription prices is **NOT** a Beta Review rejection. Mention the limitation in "What to Test" and reviewers will skip it.

---

## Phase 2: App Store path (Distribution)

Do this when you're ready to go public. All Phase 0 items must be complete.

### 2.1 Pre-flight checklist

#### Marketing assets

- [ ] App icon (1024×1024) — uploaded in App Information
- [ ] Screenshots:
  - [ ] 6.9" iPhone (iPhone 16 Pro Max) — required, min 2 max 10
  - [ ] 6.1" iPhone (iPhone 15/16) — recommended
  - [ ] iPad 13" — only if you ship iPad
- [ ] App Preview video (optional, 15–30s, hugely improves conversion)
- [ ] Promotional Text (170 chars, can be updated post-release)
- [ ] Description (4000 chars)
- [ ] Keywords (100 chars total, comma-separated, no spaces around commas)
- [ ] Support URL
- [ ] Marketing URL (optional)

#### App Privacy nutrition label

App Store Connect → App Privacy. For KindCaddy declare:

- **Linked to user**: name, email address, user ID, purchase history, precise location/weather context, audio data for recorded voice features, product interaction/app usage analytics.
- **Not linked to user**: diagnostics and crash data, unless your crash provider or logs attach user IDs.
- **Not used for tracking**: no IDFA, ad network, or third-party advertising use.
- **Not collected**: contacts, browsing history, health, fitness, financial, or sensitive information.
- **Microphone access**: purpose = "App functionality" for voice caddy requests.
- **Sign-In with Apple + Google**: user IDs and email addresses.
- **Privacy manifest**: `ios/KindCaddy/KindCaddy/PrivacyInfo.xcprivacy` should stay aligned with this App Privacy declaration.

### 2.2 Distribution page setup

App Store Connect → **Distribution** → iOS App → **1.0 Prepare for Submission**:

- [ ] **General Information**: name, subtitle, primary category (Sports), secondary (Health & Fitness)
- [ ] **Pricing and Availability**: free app (subscriptions handle monetization), all countries or specific markets
- [ ] **App Privacy**: nutrition label complete (see above)
- [ ] **Build**: attach the same build you sent to TestFlight
- [ ] **In-App Purchases and Subscriptions**: **+** → attach `kindcaddy.pro.monthly` and `kindcaddy.pro.yearly`
  - Their state changes from "Ready to Submit" → "Waiting for Review"
  - **Required on first version** — IAPs cannot be reviewed standalone for a brand-new app
- [ ] **Age Rating**: complete questionnaire (likely 4+)
- [ ] **App Review Information**:
  - [ ] **Sign-In Required** → demo account credentials
  - [ ] Contact info (real phone + email reviewer can reach)
  - [ ] **Notes** for reviewer:

    ```
    KindCaddy is an AI golf caddy powered by GPT-4o on the backend.
    
    To test:
    1. Sign in with demo account (or create new account / Apple Sign-In)
    2. Create a golfer profile (handicap, club distances)
    3. Start a round on any course
    4. Tap the microphone and ask: "I have 150 yards uphill into the wind, what club?"
    5. Log shots and scores hole-by-hole
    6. Open Paywall → see Pro subscription tiers (monthly/yearly)
    
    The app does not support user-to-user sharing or social features.
    All advice is AI-generated for the signed-in user only.
    ```

- [ ] **Version Release**:
  - [ ] **Manual release** recommended for v1.0 (control launch day)
  - [ ] **Phased release** ON (rolls out 1%→2%→5%→10%→20%→50%→100% over 7 days; pausable)

### 2.3 Submit for review

Once every required field is green, the **Submit for Review** button becomes active (top right).

- [ ] Click Submit
- [ ] Answer compliance questions:
  - **Export Compliance**: standard HTTPS = exempt under ECCN 5D992
  - **Content Rights**: yes, you own/license all content
  - **Advertising Identifier (IDFA)**: no (unless you've added an ad SDK)
- [ ] Status: **Waiting for Review** → **In Review** → **Pending Developer Release** (manual) or **Ready for Sale** (automatic)

### 2.4 Common App Review rejections

| Guideline | Common cause | Mitigation |
|---|---|---|
| **2.1 Performance** | Reviewer can't sign in / app crashes | Demo account + smoke test on fresh device |
| **2.1 Performance** | IAPs don't load | Confirm Paid Apps Agreement Active + IAPs attached to version |
| **3.1.1 IAP** | Subscription terms not visible on paywall | Show price/length/auto-renewal text + Terms/Privacy links |
| **3.1.2 Subscription** | Free trial length unclear | "5 completed rounds free, then $X/month, auto-renews" |
| **4.8 Sign in with Apple** | Offering Google but not Apple Sign-In | Already implemented in `AuthManager.swift` |
| **5.1.1 Privacy** | Vague permission strings | Microphone string must explain real use |
| **5.1.1 Privacy** | App Privacy nutrition label inaccurate | Audit declarations against actual data flows |

---

## Phase 3: Post-approval

### 3.1 Release

- **Manual release**: click "Release this Version" when you're ready
- **Automatic**: app goes live within ~1h of approval
- **Phased**: starts at 1%, you control pace via App Store Connect

### 3.2 Day-1 monitoring

- [ ] Watch crash reports in App Store Connect → Analytics → Crashes
- [ ] Watch backend error rate via your ops dashboard (`scripts/readiness_check.py`)
- [ ] Have a rollback plan ready: pause phased release if crash rate > 1%

### 3.3 Search indexing

- App Store search indexing takes ~24–48h after release
- Don't panic if your app doesn't appear in search results immediately

---

## Realistic timeline

| Day | Activity |
|---|---|
| **Day 0** | Submit external TestFlight + start Paid Apps Agreement + write privacy/terms |
| **Day 1–2** | Beta Review approves; testers install. Banking verification clears. |
| **Day 2–4** | Paid Apps Agreement Active. Confirm prices load in TestFlight. Prepare screenshots, descriptions, App Privacy. |
| **Day 5** | Submit v1.0 + IAPs to App Review |
| **Day 6–7** | App Review completes |
| **Day 7+** | Manual release → live on App Store |

**Total: ~1 week to public launch**, assuming no rejection round-trips. Add 2–4 days of buffer for first-app submissions (rejections are common and add 24–48h each).

---

## Templates

### Beta App Description (paste into Test Information)

```text
KindCaddy is an AI-powered golf caddy in your pocket. Tap the mic and ask anything you'd ask a human caddy — "I have 165 yards uphill into a two-club wind, what should I hit?" — and KindCaddy gives you a club, line, and shot shape recommendation in seconds.

Under the hood, KindCaddy combines your golfer profile (handicap, club carries, miss tendencies, shot shape) with live conditions (wind, elevation, temperature, lie) and the current state of your round to produce advice tailored to you, not generic chart numbers.

What's in this beta:
• Voice-first caddy advice powered by GPT-4o
• Personalized club selection accounting for wind, altitude, temperature, lie, and back-9 fatigue
• Live round tracking — log shots, scores, and miss patterns hole-by-hole
• Post-round insights that get smarter the more you play
• Sign in with Apple or Google
• Five completed trial rounds, then optional Pro subscription (monthly or yearly)

We're looking for honest feedback on advice quality, voice accuracy, and overall feel during a real round.
```

### What to Test (paste into each new build)

```text
Things we'd love feedback on this build:

1. Onboarding — Sign in (Apple, Google, or email) and create your golfer profile. Does anything feel confusing?
2. Starting a round — Pick a course, set the tee box, start a round.
3. Asking for advice — Tap the mic and ask realistic questions: "What club for 150 uphill?", "Wind in my face, adjust club?", "I missed left three holes in a row, what now?"
4. Logging shots and scores — Is it fast enough to keep up with pace of play?
5. Voice accuracy — Did speech recognition catch your full question, especially with wind or noise?
6. Performance and battery — How does the app feel over a 4–5 hour round? Any crashes, lag, or major battery drain?

Known limitations in this build:
• Subscription prices may not appear yet — we're finalizing App Store agreements. The free trial (5 completed rounds) works for everyone in beta.
• Course database is limited; if you don't see your course, tell us the name and we'll add it.

Send feedback by shaking your iPhone in TestFlight, or email feedback@kindcaddy.app.
```

### App Store Description (paste into Distribution → Description)

```text
KindCaddy is your AI caddy on the bag — the smartest way to play your best round.

VOICE-FIRST CADDY ADVICE
Tap the mic and ask anything you'd ask a human caddy. "165 uphill into the wind, what club?" "Wind just shifted, adjust?" "I keep missing left, what now?" KindCaddy answers in seconds with a specific club, line, and shot shape — tailored to your game, not generic chart numbers.

PERSONALIZED CLUB SELECTION
KindCaddy learns your real club carries, miss tendencies, and shot shape, then factors in:
• Wind speed and direction
• Elevation change
• Temperature and altitude
• Lie type (fairway, rough, sand, hardpan)
• Back-9 fatigue
• Hazards and pin position

LIVE ROUND TRACKING
Log shots and scores hole-by-hole. KindCaddy spots patterns mid-round and gives you strategic alerts ("you've missed left three holes in a row — aim 5 yards right of center on this tee").

POST-ROUND INSIGHTS
After every round, KindCaddy updates your profile with what it learned — your real driver carry on a 70°F day, your tendency to push 7-irons, your scoring trend on par 3s. The more you play, the smarter your caddy gets.

PRO SUBSCRIPTION
Five completed rounds to try KindCaddy. After that:
• KindCaddy Pro Monthly: $X.XX/month
• KindCaddy Pro Yearly: $XX.XX/year (save XX%)

Auto-renews unless canceled. Cancel anytime in Settings.

Privacy Policy: https://kindcaddy.app/privacy
Terms of Use: https://kindcaddy.app/terms
```

---

## Quick reference: where things live in App Store Connect

| Task | Where |
|---|---|
| Upload build | Xcode → Archive → Distribute (or `xcrun altool`) |
| Beta tester management | TestFlight tab |
| Beta App Review submission | TestFlight → External Group → Builds → + |
| App Store metadata | Distribution tab |
| App Store submission | Distribution → 1.0 → Submit for Review (top right) |
| Subscription configuration | Monetization → Subscriptions |
| Pricing | Distribution → Pricing and Availability |
| Banking, tax, agreements | Business |
| Crash reports | Analytics → Crashes |
| Sales / downloads | Analytics → Metrics |

---

## When in doubt

- **TestFlight rejection?** Read the Resolution Center message — it's specific. 90% of fixes are <1 hour.
- **App Review rejection?** Same — reply in Resolution Center first, no need to resubmit unless they explicitly ask for a new build.
- **IAPs not loading anywhere?** It's almost always Paid Apps Agreement. Check Business page first.
- **Build not appearing in TestFlight?** Wait 30 min after the processing email; if still missing, check email for compliance issues from Apple.
