"""
Complete end-to-end test data for StandupSync.

Accounts created:
  Manager  → manager@standupsync.test  / Test@1234  (Starter plan — full feature access)
  Member 1 → alice@standupsync.test    / Test@1234  (Alice Chen    — rate set, hours confirmed)
  Member 2 → bob@standupsync.test      / Test@1234  (Bob Kumar     — rate set, hours NOT confirmed)
  Member 3 → carlos@standupsync.test   / Test@1234  (Carlos Silva  — no rate set)
  Member 4 → diana@standupsync.test    / Test@1234  (Diana Park    — rate set, hours confirmed)

Creates:
  - 1 Starter-plan team "[TEST] Alpha Engineering Team"
  - 4 configurable team questions (yesterday, today, blockers, wins)
  - 30 days of check-in history (Alice ~90%, Bob ~70%, Carlos ~55%, Diana ~80%)
  - 8 blockers across all 4 statuses (open ×4, acknowledged ×1, in_progress ×1, resolved ×2)
  - Blocker comments on 3 blockers
  - Manager resolutions on 2 resolved blockers

Run:
  python3 seed_complete_test_data.py

Remove all test data:
  python3 remove_test_data.py
"""

import asyncio
import uuid
from datetime import datetime, date, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from database import DATABASE_URL
from models import (
    User,
    Team,
    TeamMember,
    TeamQuestion,
    Checkin,
    CheckinAnswer,
    Blocker,
    BlockerComment,
    BlockerResolution,
)
from auth import hash_password

# ─── Test data configuration ─────────────────────────────────────────────────

MANAGER_EMAIL = "manager@standupsync.test"
MANAGER_NAME  = "Sarah Mitchell"
TEAM_NAME     = "[TEST] Alpha Engineering Team"
TEST_PASSWORD = "Test@1234"
SEED_DAYS     = 30

MEMBER_CONFIGS = [
    {
        "email":           "alice@standupsync.test",
        "name":            "Alice Chen",
        "role_desc":       "Senior Developer",
        "hourly_rate":     650.0,
        "currency":        "INR",
        "hours_per_day":   8.0,
        "hours_confirmed": True,    # ✓ green badge in Cost Intelligence
    },
    {
        "email":           "bob@standupsync.test",
        "name":            "Bob Kumar",
        "role_desc":       "DevOps Engineer",
        "hourly_rate":     550.0,
        "currency":        "INR",
        "hours_per_day":   7.0,
        "hours_confirmed": False,   # ⚠ amber badge + hours-confirm banner on check-in
    },
    {
        "email":           "carlos@standupsync.test",
        "name":            "Carlos Silva",
        "role_desc":       "QA Lead",
        "hourly_rate":     None,    # — missing-rate warning in Cost Intelligence
        "currency":        "INR",
        "hours_per_day":   None,
        "hours_confirmed": False,
    },
    {
        "email":           "diana@standupsync.test",
        "name":            "Diana Park",
        "role_desc":       "Product Manager",
        "hourly_rate":     450.0,
        "currency":        "INR",
        "hours_per_day":   6.0,
        "hours_confirmed": True,    # ✓ confirmed
    },
]

# ─── Check-in text banks ──────────────────────────────────────────────────────
#
# Patterns intentionally repeated so the AI Task Radar (Gemini) identifies
# automation opportunities across all members:
#   • "compile weekly status report"  → Alice, Bob, Diana      → top finding
#   • "manual regression testing"     → Alice, Carlos          → 2nd finding
#   • "deploy to staging"             → Alice, Bob             → 3rd finding
#   • "configure dev environment"     → Bob, Carlos            → 4th finding
#   • "compile competitor analysis"   → Diana                  → 5th finding
#   • "manual test data setup"        → Carlos                 → 6th finding

_ALICE_Y = [
    "Manually deployed the latest build to the staging server and reconfigured environment variables from scratch",
    "Compiled the weekly status report by pulling data from Jira, GitHub, and Confluence and emailed it to stakeholders",
    "Ran full manual regression testing suite on the checkout and payment modules for the release",
    "Fixed broken staging environment configuration after the automated deploy reset all env vars overnight",
    "Updated API documentation by manually reviewing all v2 endpoints and writing up the response schemas",
    "Manually synced production database snapshot to staging environment for QA team regression testing",
    "Generated sprint velocity report by manually aggregating story points from Jira and GitHub PRs",
    "Ran manual smoke tests on the authentication flow and documented all edge case failures found",
]
_ALICE_T = [
    "Deploy the hotfix to staging server and run manual smoke tests on all critical payment paths",
    "Update the weekly client status report by pulling from all project tracking and analytics sources",
    "Run manual regression testing on user authentication module and document any failures found",
    "Manually configure staging environment variables to match updated production configuration",
    "Compile sprint metrics report manually from Jira and share with the team in Slack today",
    "Deploy backend changes to staging and run end to end manual tests across all user flows",
    "Update deployment runbook document with the corrected steps from yesterday's production release",
    "Run full manual regression testing on the new payment gateway integration before go-live",
]

_BOB_Y = [
    "Manually SSHed into the production server to deploy the v2.3.1 release and verified all services started",
    "Set up a completely fresh development environment for the new frontend contractor from scratch",
    "Manually renewed the expired SSL certificate on staging server after the Datadog alert triggered",
    "Configured CloudWatch and Datadog monitoring dashboards and alerts by hand for the new microservice",
    "Updated the deployment runbook documentation after yesterday's manual production rollout process",
    "Manually patched Docker Compose file to resolve Node 18 dependency conflict on developer machines",
    "Set up Jenkins CI/CD pipeline configuration manually for the new payments-service repository",
    "Manually rotated API keys across all staging and production services and updated Vault configuration",
]
_BOB_T = [
    "Manually deploy the new release to production and verify all microservices come up healthy",
    "Set up fresh development environment for the new QA contractor starting this week",
    "Configure monitoring dashboards and alerting thresholds for the new data pipeline deployment",
    "Check and manually renew any SSL certificates that are expiring in the next 30 days",
    "Update the deployment runbook with the corrected rollback steps from the last production incident",
    "Manually sync all environment variables from Vault to staging and production server configs",
    "Set up a clean test environment for QA team to validate the sprint deliverables",
    "Review and manually rotate all service credentials that are approaching their 90-day expiry",
]

_CARLOS_Y = [
    "Manually set up the entire test environment from scratch again for the new sprint QA cycle",
    "Ran the full regression testing suite manually across all critical user workflows and documented results",
    "Updated all test case documentation by hand based on the new feature requirements from product",
    "Rebuilt the local development environment from scratch after the macOS Sonoma update broke all tooling",
    "Manually created and loaded complete test data sets for all test scenarios in the current sprint",
    "Ran manual end to end testing on all the dashboard and reporting UI flows and logged 12 issues",
    "Updated the master test plan document with new test cases written for the payment integration feature",
    "Manually verified all REST API responses match the documented schema across all 47 endpoints",
]
_CARLOS_T = [
    "Set up fresh test environment for QA validation of the new release candidate build today",
    "Run manual regression testing on all critical user paths before the 5pm release window",
    "Update the test case documentation with all the failures and edge cases from yesterday sessions",
    "Manually create and load test data for the integration testing scenarios planned this afternoon",
    "Run full end to end manual testing on the checkout and payment gateway integration flows",
    "Write up the full test execution summary report and share with the team on Confluence today",
    "Configure the automated test environment from the runbook for the new developer joining Monday",
    "Manually verify all the edge cases in the new two-factor authentication implementation",
]

_DIANA_Y = [
    "Manually compiled the weekly competitor analysis by visiting all 6 competitor websites and social channels",
    "Collected and synthesized customer feedback from Intercom, Zendesk, and the NPS survey tool by hand",
    "Prepared the weekly product metrics dashboard by pulling data manually from Google Analytics and Mixpanel",
    "Updated the product roadmap presentation by hand based on 4 hours of stakeholder interview notes",
    "Manually transcribed and uploaded all customer interview recordings to the Confluence product wiki",
    "Compiled the monthly user engagement report from Google Analytics, Amplitude, and Mixpanel manually",
    "Updated the feature prioritization matrix in the shared spreadsheet manually after reviewing 80 user tickets",
    "Manually aggregated all Zendesk customer support tickets this week to identify recurring product gaps",
]
_DIANA_T = [
    "Compile the weekly competitor analysis from all competitor websites and their social media channels",
    "Collect and organize all customer feedback from Intercom, Zendesk, and NPS tool into one doc",
    "Prepare the product metrics report by pulling data manually from all analytics tools this morning",
    "Update the product roadmap slides based on the stakeholder alignment meeting notes from yesterday",
    "Write up the customer interview summary and share with the entire product and design team today",
    "Manually compile all the user engagement data from analytics tools for the monthly board report",
    "Update the feature prioritization backlog based on this week's customer feedback analysis results",
    "Compile all customer support insights from Zendesk for the quarterly product strategy review meeting",
]

# (yesterday_phrases, today_phrases) per member index
MEMBER_TEXTS = [
    (_ALICE_Y,  _ALICE_T),
    (_BOB_Y,    _BOB_T),
    (_CARLOS_Y, _CARLOS_T),
    (_DIANA_Y,  _DIANA_T),
]


def _should_submit(member_idx: int, day_idx: int) -> bool:
    """
    Check-in submission rates:
      Alice  (0): ~90% — skips every 10th day
      Bob    (1): ~70% — skips every 3rd day
      Carlos (2): ~55% — submits odd-indexed days
      Diana  (3): ~80% — skips every 5th day
    """
    if member_idx == 0:
        return day_idx % 10 != 0          # 27/30 ≈ 90%
    if member_idx == 1:
        return day_idx % 3 != 2           # 20/30 ≈ 67%
    if member_idx == 2:
        return day_idx % 2 == 1           # 15/30 = 50% (odd days only)
    return day_idx % 5 != 0              # 24/30 = 80%


# ─── DB engine ────────────────────────────────────────────────────────────────

_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: "",
        "statement_cache_size": 0,
    },
)
_Session = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


# ─── Main seed function ───────────────────────────────────────────────────────

async def main() -> None:
    async with _Session() as session:
        async with session.begin():

            # ── Guard: idempotent ─────────────────────────────────────────────
            existing = await session.execute(
                select(User).where(User.email == MANAGER_EMAIL)
            )
            if existing.scalar_one_or_none():
                print(
                    f"⚠️  Test data already exists ({MANAGER_EMAIL} found).\n"
                    "   Run `python3 remove_test_data.py` first to reseed."
                )
                return

            hashed_pw = hash_password(TEST_PASSWORD)
            today     = date.today()
            now       = datetime.utcnow()

            # ── 1. Create manager (Starter plan) ──────────────────────────────
            manager = User(
                email=MANAGER_EMAIL,
                name=MANAGER_NAME,
                password=hashed_pw,
                role="manager",
                plan="starter",
                plan_status="active",
                created_at=now - timedelta(days=40),
            )
            session.add(manager)
            await session.flush()

            # ── 2. Create member users ────────────────────────────────────────
            members: list[User] = []
            for cfg in MEMBER_CONFIGS:
                u = User(
                    email=cfg["email"],
                    name=cfg["name"],
                    password=hashed_pw,
                    role="member",
                    plan="free",
                    plan_status="active",
                    created_at=now - timedelta(days=37),
                )
                session.add(u)
                members.append(u)
            await session.flush()

            # ── 3. Create team ────────────────────────────────────────────────
            team = Team(
                name=TEAM_NAME,
                manager_id=manager.id,
                team_type="software",
                hourly_rate=600.0,
                currency="INR",
                timezone="Asia/Kolkata",
                created_at=now - timedelta(days=38),
            )
            session.add(team)
            await session.flush()

            # ── 4. Create TeamMembers ─────────────────────────────────────────
            for cfg, user in zip(MEMBER_CONFIGS, members):
                tm = TeamMember(
                    team_id=team.id,
                    user_id=user.id,
                    status="active",
                    role="member",
                    hourly_rate=cfg["hourly_rate"],
                    currency=cfg["currency"],
                    hours_per_day=cfg["hours_per_day"],
                    hours_confirmed=cfg["hours_confirmed"],
                    timezone="Asia/Kolkata",
                    send_time="09:00",
                    created_at=now - timedelta(days=36),
                )
                session.add(tm)
            await session.flush()

            # ── 5. Create TeamQuestions ───────────────────────────────────────
            q_yesterday = TeamQuestion(
                team_id=team.id,
                order_index=0,
                label="What did you accomplish yesterday?",
                enabled=True,
                is_blocker_type=False,
                created_at=now - timedelta(days=36),
            )
            q_today_q = TeamQuestion(
                team_id=team.id,
                order_index=1,
                label="What will you work on today?",
                enabled=True,
                is_blocker_type=False,
                created_at=now - timedelta(days=36),
            )
            q_blockers = TeamQuestion(
                team_id=team.id,
                order_index=2,
                label="Any blockers or impediments?",
                enabled=True,
                is_blocker_type=True,
                created_at=now - timedelta(days=36),
            )
            q_wins = TeamQuestion(
                team_id=team.id,
                order_index=3,
                label="Any wins or shoutouts to share?",
                enabled=True,
                is_blocker_type=False,
                created_at=now - timedelta(days=36),
            )
            session.add_all([q_yesterday, q_today_q, q_blockers, q_wins])
            await session.flush()

            # ── 6. Create Checkins + CheckinAnswers (30 days) ─────────────────
            checkin_count = 0
            for day_idx in range(SEED_DAYS):
                target_date    = today - timedelta(days=(SEED_DAYS - 1 - day_idx))
                submitted_time = datetime.combine(target_date, datetime.min.time()).replace(
                    hour=9, minute=30, second=0
                )
                created_time   = datetime.combine(target_date, datetime.min.time()).replace(
                    hour=1, minute=0, second=0
                )

                for m_idx, (user, texts) in enumerate(zip(members, MEMBER_TEXTS)):
                    if not _should_submit(m_idx, day_idx):
                        continue

                    phrase_y = texts[0][day_idx % len(texts[0])]
                    phrase_t = texts[1][day_idx % len(texts[1])]

                    checkin = Checkin(
                        team_id=team.id,
                        user_id=user.id,
                        date=target_date,
                        checkin_token=str(uuid.uuid4()),
                        token_used=True,
                        submitted_at=submitted_time,
                        created_at=created_time,
                    )
                    session.add(checkin)
                    await session.flush()

                    session.add(CheckinAnswer(
                        checkin_id=checkin.id,
                        question_id=q_yesterday.id,
                        answer=phrase_y,
                        created_at=submitted_time,
                    ))
                    session.add(CheckinAnswer(
                        checkin_id=checkin.id,
                        question_id=q_today_q.id,
                        answer=phrase_t,
                        created_at=submitted_time,
                    ))
                    session.add(CheckinAnswer(
                        checkin_id=checkin.id,
                        question_id=q_blockers.id,
                        answer="",
                        created_at=submitted_time,
                    ))
                    session.add(CheckinAnswer(
                        checkin_id=checkin.id,
                        question_id=q_wins.id,
                        answer="",
                        created_at=submitted_time,
                    ))
                    checkin_count += 1

            await session.flush()

            # ── 7. Create Blockers ────────────────────────────────────────────
            alice, bob, carlos, diana = members

            #
            # 8 blockers: 4 open, 1 acknowledged, 1 in_progress, 2 resolved
            #

            # B1 — Open: Alice reports CI/CD issue (3 days ago)
            b1 = Blocker(
                team_id=team.id,
                user_id=alice.id,
                assigned_to=None,
                status="open",
                title="CI/CD pipeline fails on every merge to main branch",
                description=(
                    "Every time a PR is merged to main, the pipeline breaks with a flaky test runner "
                    "error. We've had to manually re-run builds 3–5 times per merge for the past week. "
                    "Root cause appears to be a race condition in the parallel test execution setup."
                ),
                created_at=now - timedelta(days=3),
                updated_at=now - timedelta(days=3),
            )
            session.add(b1)
            await session.flush()

            # B2 — Open: Bob reports production deployment requiring manual SSH (7 days ago, assigned to manager)
            b2 = Blocker(
                team_id=team.id,
                user_id=bob.id,
                assigned_to=manager.id,
                status="open",
                title="Production deployment requires manual SSH access every release",
                description=(
                    "Every production release requires manual SSH access to run the deployment script, "
                    "verify service health, and tail logs for 20 minutes. This blocks the team for 2+ hours "
                    "per release and prevents off-hours deployments. We need an automated zero-touch deploy pipeline."
                ),
                created_at=now - timedelta(days=7),
                updated_at=now - timedelta(days=7),
            )
            session.add(b2)
            await session.flush()

            # B3 — Acknowledged: Carlos reports test data setup taking too long (12 days ago)
            b3 = Blocker(
                team_id=team.id,
                user_id=carlos.id,
                assigned_to=None,
                status="acknowledged",
                title="Manual test data setup takes 2+ hours before every sprint",
                description=(
                    "At the start of every sprint, QA manually creates test accounts, loads fixture data, "
                    "and configures test environments. This process takes 2–3 hours and is error-prone — "
                    "we regularly find inconsistencies mid-sprint. A database seeding script or factory "
                    "would eliminate this overhead entirely."
                ),
                created_at=now - timedelta(days=12),
                updated_at=now - timedelta(days=10),
            )
            session.add(b3)
            await session.flush()

            # B4 — In Progress: Diana reports competitor analysis done manually (18 days ago)
            b4 = Blocker(
                team_id=team.id,
                user_id=diana.id,
                assigned_to=manager.id,
                status="in_progress",
                title="Weekly competitor analysis compiled manually every time",
                description=(
                    "Every Monday I spend 4–5 hours visiting 6 competitor websites, checking their "
                    "changelog pages, social media, and job postings to compile the weekly intelligence report. "
                    "This is a highly automatable task — tools like Crayon or a custom scraper could do this "
                    "in minutes. Currently blocking strategic planning work on Monday mornings."
                ),
                created_at=now - timedelta(days=18),
                updated_at=now - timedelta(days=5),
            )
            session.add(b4)
            await session.flush()

            # B5 — Open: Alice reports status reports compiled from 5 tools (4 days ago)
            b5 = Blocker(
                team_id=team.id,
                user_id=alice.id,
                assigned_to=None,
                status="open",
                title="Weekly status reports compiled manually from 5 different tools",
                description=(
                    "Every Friday I spend 90 minutes pulling data from Jira, GitHub, Confluence, "
                    "Google Analytics, and Datadog to compile the team status report. This is purely "
                    "mechanical data aggregation — the same steps every week. A Zapier workflow or "
                    "a simple script could auto-generate 80% of this report."
                ),
                created_at=now - timedelta(days=4),
                updated_at=now - timedelta(days=4),
            )
            session.add(b5)
            await session.flush()

            # B6 — Open: Diana reports onboarding docs outdated (6 days ago)
            b6 = Blocker(
                team_id=team.id,
                user_id=diana.id,
                assigned_to=None,
                status="open",
                title="Onboarding documentation outdated — new hires trained manually each time",
                description=(
                    "Our onboarding docs haven't been updated in 6 months. Every new hire requires "
                    "2–3 days of manual shadowing and live walkthroughs because the written docs are "
                    "wrong or missing. We need to overhaul the docs and create a self-service onboarding "
                    "checklist so new hires can get up to speed independently."
                ),
                created_at=now - timedelta(days=6),
                updated_at=now - timedelta(days=6),
            )
            session.add(b6)
            await session.flush()

            # B7 — Resolved: Bob's SSL certificate issue (22 days ago, resolved 14 days ago)
            b7 = Blocker(
                team_id=team.id,
                user_id=bob.id,
                assigned_to=None,
                status="resolved",
                title="SSL certificate expired on staging server causing QA test failures",
                description=(
                    "The SSL certificate on staging-api.company.com expired overnight, causing all QA "
                    "automated tests to fail with SSL handshake errors. Manually renewed via cPanel. "
                    "Root cause: no automated certificate renewal process. Certbot or AWS ACM "
                    "should handle this automatically going forward."
                ),
                created_at=now - timedelta(days=22),
                updated_at=now - timedelta(days=14),
                resolved_at=now - timedelta(days=14),
            )
            session.add(b7)
            await session.flush()

            # B8 — Resolved: Carlos's dev environment breaking after OS update (20 days ago, resolved 12 days ago)
            b8 = Blocker(
                team_id=team.id,
                user_id=carlos.id,
                assigned_to=None,
                status="resolved",
                title="Local development environment breaks after OS update on all team machines",
                description=(
                    "After the latest macOS Sonoma update, all team members' local dev environments "
                    "stopped working due to broken Python symlinks and mismatched Node.js versions. "
                    "Each person had to manually rebuild their environment from scratch — "
                    "total team productivity loss of ~16 engineering hours over 2 days."
                ),
                created_at=now - timedelta(days=20),
                updated_at=now - timedelta(days=12),
                resolved_at=now - timedelta(days=12),
            )
            session.add(b8)
            await session.flush()

            # ── 8. Add Blocker Comments ───────────────────────────────────────

            # Comments on B1 (CI/CD issue)
            session.add(BlockerComment(
                blocker_id=b1.id,
                user_id=bob.id,
                comment=(
                    "I've seen this too on my side. It seems to be specifically the Jest test runner "
                    "timing out when it runs in parallel with the lint step. Reproduces consistently "
                    "when two PRs are merged within 60 seconds of each other."
                ),
                created_at=now - timedelta(days=2, hours=4),
            ))
            session.add(BlockerComment(
                blocker_id=b1.id,
                user_id=manager.id,
                comment=(
                    "Good catch both of you. I'll create a P1 ticket and assign it to the DevOps "
                    "track for next sprint. In the meantime, please serialize merges by checking "
                    "with the team in #engineering before merging any PR."
                ),
                created_at=now - timedelta(days=2, hours=1),
            ))

            # Comments on B3 (test data setup)
            session.add(BlockerComment(
                blocker_id=b3.id,
                user_id=alice.id,
                comment=(
                    "We had exactly this problem last quarter. I started a seed script in "
                    "scripts/seed_qa_data.py — it's half-finished but the user creation part works. "
                    "Happy to pair with you to finish it this week."
                ),
                created_at=now - timedelta(days=11, hours=6),
            ))
            session.add(BlockerComment(
                blocker_id=b3.id,
                user_id=manager.id,
                comment=(
                    "Thanks Alice! Let's prioritize finishing that seed script in the next sprint. "
                    "Carlos — please add it as a tech-debt ticket in Jira so we can track it properly."
                ),
                created_at=now - timedelta(days=10, hours=2),
            ))

            # Comments on B2 (production deployment)
            session.add(BlockerComment(
                blocker_id=b2.id,
                user_id=manager.id,
                comment=(
                    "This is on my radar. I've started exploring GitHub Actions for the zero-touch "
                    "deployment pipeline. Target is to have a working draft by end of next sprint. "
                    "Will share the design doc in the #devops channel for feedback."
                ),
                created_at=now - timedelta(days=5, hours=3),
            ))

            await session.flush()

            # ── 9. Add Blocker Resolutions (for resolved blockers) ────────────

            session.add(BlockerResolution(
                blocker_id=b7.id,
                manager_id=manager.id,
                unblock_instructions=(
                    "RESOLVED. Actions taken:\n"
                    "1. Manually renewed the expired certificate via cPanel (immediate fix).\n"
                    "2. Installed and configured certbot on the staging server with auto-renewal cron.\n"
                    "3. Set up a Datadog monitor to alert 30 days before any certificate expiry.\n"
                    "4. Updated the Server Maintenance runbook in Confluence with these steps.\n\n"
                    "Going forward: certbot will auto-renew all staging certificates 60 days before "
                    "expiry. The Datadog alert gives us a 30-day warning buffer as a second layer."
                ),
                created_at=now - timedelta(days=14),
            ))

            session.add(BlockerResolution(
                blocker_id=b8.id,
                manager_id=manager.id,
                unblock_instructions=(
                    "RESOLVED. Root cause: macOS Sonoma changed symlink behavior for system Python "
                    "and broke the nvm Node.js version manager for several team members.\n\n"
                    "Actions taken:\n"
                    "1. Created a Dockerized development environment (docker-compose.yml committed to main).\n"
                    "2. Updated the Getting Started guide in Confluence with Docker setup instructions.\n"
                    "3. All team members should now use 'docker-compose up' instead of local setup.\n"
                    "4. Created a .devcontainer config for VS Code users who prefer that workflow.\n\n"
                    "Action required from everyone: Pull latest main and follow the updated setup guide "
                    "at docs/getting-started.md. Delete your old venv and nvm setup once Docker works."
                ),
                created_at=now - timedelta(days=12),
            ))

            await session.flush()

    # ── Print summary ─────────────────────────────────────────────────────────
    open_count  = 4
    ack_count   = 1
    ip_count    = 1
    res_count   = 2

    print()
    print("✅  Complete test data seeded successfully!")
    print()
    print(f"  Team    : {TEAM_NAME}  (Manager has Starter plan — full AI Radar access)")
    print(f"  Manager : {MANAGER_EMAIL}  →  {MANAGER_NAME}  |  password: {TEST_PASSWORD}")
    print()

    rates = [
        f"₹{c['hourly_rate']}/hr" if c["hourly_rate"] else "no rate set"
        for c in MEMBER_CONFIGS
    ]
    hrs_labels = [
        f"{c['hours_per_day']}h/day" if c["hours_per_day"] else "not set"
        for c in MEMBER_CONFIGS
    ]
    conf_labels = ["✓ confirmed" if c["hours_confirmed"] else "not confirmed" for c in MEMBER_CONFIGS]
    pct = ["~90%", "~70%", "~55%", "~80%"]

    for i, cfg in enumerate(MEMBER_CONFIGS):
        print(
            f"  Member  : {cfg['email']}  →  {cfg['name']} ({cfg['role_desc']})"
            f"  |  rate: {rates[i]}  |  hours: {hrs_labels[i]} ({conf_labels[i]})"
            f"  |  check-in rate: {pct[i]}"
        )

    print()
    print(f"  Check-ins : {checkin_count} records over {SEED_DAYS} days")
    print(
        f"  Blockers  : 8 total | "
        f"open: {open_count}, acknowledged: {ack_count}, in_progress: {ip_count}, resolved: {res_count}"
    )
    print("  Comments  : 5 blocker comments across 3 blockers")
    print("  Resolutions: 2 manager resolutions on resolved blockers")
    print()
    print("  LLM Provider : gemini (gemini-1.5-flash) — real-time API calls enabled")
    print("  AI Radar     : Use the admin/run endpoint or enable the schedule to trigger analysis")
    print()
    print("  Cost Intelligence:")
    print("    Alice  → ₹5,200/day (8h × ₹650/hr)  — rate + hours confirmed")
    print("    Bob    → ₹3,850/day (7h × ₹550/hr)  — rate set, hours NOT confirmed")
    print("    Diana  → ₹2,700/day (6h × ₹450/hr)  — rate + hours confirmed")
    print("    Carlos → no rate — shows missing-rate warning")
    print()
    print("  To remove all test data : python3 remove_test_data.py")
    print()

    await _engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
