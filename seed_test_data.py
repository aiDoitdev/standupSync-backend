"""
Seed test data for StandupSync feature testing.

Test accounts created:
  Manager  → testmanager@test.com   / Test@1234
  Member 1 → testmember1@test.com   / Test@1234  (Alice Chen — rate set, hours confirmed)
  Member 2 → testmember2@test.com   / Test@1234  (Bob Kumar  — rate set, hours NOT confirmed)
  Member 3 → testmember3@test.com   / Test@1234  (Carlos Silva — NO rate set)

Creates:
  - 1 Starter-plan team "[TEST] Test Team Alpha"
  - 3 configurable team questions
  - 30 days of check-in history  (Alice ~87%,  Bob ~67%,  Carlos ~50%)
  - 5 blockers in mixed states   (open ×2, acknowledged, in_progress, resolved)

Remove all test data:
  python3 remove_test_data.py

Run:
  python3 seed_test_data.py
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
)
from auth import hash_password

# ─── Test data configuration ─────────────────────────────────────────────────

MANAGER_EMAIL = "testmanager@test.com"

MEMBER_CONFIGS = [
    {
        "email": "testmember1@test.com",
        "name": "Alice Chen",
        "hourly_rate": 500.0,
        "currency": "INR",
        "hours_per_day": 8.0,
        "hours_confirmed": True,            # ✓ green badge in Cost Intelligence
    },
    {
        "email": "testmember2@test.com",
        "name": "Bob Kumar",
        "hourly_rate": 600.0,
        "currency": "INR",
        "hours_per_day": 7.0,
        "hours_confirmed": False,           # ⚠ amber badge + hours-confirm banner on check-in
    },
    {
        "email": "testmember3@test.com",
        "name": "Carlos Silva",
        "hourly_rate": None,                # — missing-rate warning in Cost Intelligence
        "currency": "INR",
        "hours_per_day": None,
        "hours_confirmed": False,
    },
]

TEST_PASSWORD = "Test@1234"
TEAM_NAME     = "[TEST] Test Team Alpha"
SEED_DAYS     = 30           # full 30-day reports window

# ─── Realistic check-in text for Automation Radar ────────────────────────────
#
# These phrases are designed to produce clear repeated-pattern signals:
#   "update status report"   → all 3 members   → top finding
#   "deploy to staging"      → Alice            → second finding
#   "update project tracker" → Bob              → third finding
#   "set up dev environment" → Carlos + Bob     → fourth finding
#   "manual regression test" → Alice + Carlos   → fifth finding

_ALICE_Y = [
    "Deployed latest build to staging server manually",
    "Updated weekly status report for client presentation",
    "Ran manual regression testing on the payment module",
    "Wrote deployment runbook and environment setup notes",
    "Fixed staging environment configuration after reset",
    "Updated status report and shared it with the team",
    "Performed manual smoke tests on the checkout flow",
    "Reviewed and updated all project documentation manually",
]
_ALICE_T = [
    "Deploy latest build to staging server again today",
    "Update client status report after morning sync call",
    "Run manual regression tests on the order tracking module",
    "Sync staging environment configuration with production",
    "Update deployment runbook with the latest step changes",
    "Manual regression testing on user authentication flow",
    "Deploy hotfix to staging and run manual smoke tests",
    "Update weekly status report with sprint velocity data",
]

_BOB_Y = [
    "Updated project tracker and all task statuses manually",
    "Prepared weekly progress report for client review meeting",
    "Manually synced client feedback into the task list",
    "Updated status report and emailed stakeholders manually",
    "Configured test environment setup completely from scratch",
    "Manually updated all sprint tickets with latest status",
    "Wrote and sent out the weekly team progress update email",
    "Updated project tracker with all completed task items",
]
_BOB_T = [
    "Update project tracker with today task assignments",
    "Prepare client weekly progress report for review",
    "Configure local test environment completely from scratch",
    "Manually update all task statuses in the project tracker",
    "Write and send weekly status update email to stakeholders",
    "Sync client feedback manually into task management system",
    "Update sprint board with completed and pending items",
    "Manually compile all metrics for the weekly client report",
]

_CARLOS_Y = [
    "Set up local development environment completely from scratch",
    "Ran manual regression testing on all UI components today",
    "Updated weekly status report and shared it with the team",
    "Rewrote all test setup documentation manually from notes",
    "Rebuilt dev environment after OS update broke the config",
    "Manual regression testing on all dashboard page flows",
    "Updated requirements document with all the latest changes",
    "Manually tested all form validations completely end to end",
]
_CARLOS_T = [
    "Set up dev environment again with all correct dependencies",
    "Manual regression testing on all the updated UI flows",
    "Update weekly report with completed testing session notes",
    "Rebuild test environment after configuration changes broke it",
    "Run manual end to end tests on all the new features",
    "Write up test results and share with team manually today",
    "Set up fresh test environment for the new sprint start",
    "Manual regression testing on the payment integration flow",
]

# (yesterday_phrases, today_phrases) per member
MEMBER_TEXTS = [
    (_ALICE_Y, _ALICE_T),
    (_BOB_Y,   _BOB_T),
    (_CARLOS_Y, _CARLOS_T),
]


def _should_submit(member_idx: int, day_idx: int) -> bool:
    """
    Determines whether a member submits a check-in on a given day.
    Alice:  skips every 8th day  → ~87% rate (26/30)
    Bob:    skips every 3rd day  → ~67% rate (20/30)
    Carlos: submits even days    → ~50% rate (15/30)
    """
    if member_idx == 0:
        return day_idx % 8 != 0      # skips days 0, 8, 16, 24 → 4 misses
    if member_idx == 1:
        return day_idx % 3 != 2      # skips days 2, 5, 8, ...  → 10 misses
    return day_idx % 2 == 0          # even-indexed days only   → 15 submits


# ─── DB engine (PgBouncer-compatible) ────────────────────────────────────────

_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: "",
    },
)
_Session = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


# ─── Main seed function ───────────────────────────────────────────────────────

async def main() -> None:
    async with _Session() as session:
        async with session.begin():

            # ── Guard: idempotent — skip if already seeded ────────────────────
            existing = await session.execute(
                select(User).where(User.email == MANAGER_EMAIL)
            )
            if existing.scalar_one_or_none():
                print(
                    "⚠️  Test data already exists (testmanager@test.com found).\n"
                    "   Run `python3 remove_test_data.py` first to reseed."
                )
                return

            hashed_pw = hash_password(TEST_PASSWORD)
            today     = date.today()
            now       = datetime.utcnow()

            # ── 1. Create users ───────────────────────────────────────────────
            manager = User(
                email=MANAGER_EMAIL,
                name="Test Manager",
                password=hashed_pw,
                role="manager",
                created_at=now - timedelta(days=35),
            )
            session.add(manager)
            await session.flush()

            members: list[User] = []
            for cfg in MEMBER_CONFIGS:
                u = User(
                    email=cfg["email"],
                    name=cfg["name"],
                    password=hashed_pw,
                    role="member",
                    created_at=now - timedelta(days=32),
                )
                session.add(u)
                members.append(u)
            await session.flush()

            # ── 2. Create team (Starter plan, active) ─────────────────────────
            team = Team(
                name=TEAM_NAME,
                manager_id=manager.id,
                plan="starter",
                plan_status="active",
                team_type="software",
                hourly_rate=500.0,
                currency="INR",
                created_at=now - timedelta(days=33),
            )
            session.add(team)
            await session.flush()

            # ── 3. Create TeamMembers ─────────────────────────────────────────
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
                    created_at=now - timedelta(days=31),
                )
                session.add(tm)
            await session.flush()

            # ── 4. Create TeamQuestions ───────────────────────────────────────
            q_yesterday = TeamQuestion(
                team_id=team.id,
                order_index=0,
                label="What did you accomplish yesterday?",
                enabled=True,
                is_blocker_type=False,
                created_at=now - timedelta(days=31),
            )
            q_today_q = TeamQuestion(
                team_id=team.id,
                order_index=1,
                label="What will you work on today?",
                enabled=True,
                is_blocker_type=False,
                created_at=now - timedelta(days=31),
            )
            q_blockers = TeamQuestion(
                team_id=team.id,
                order_index=2,
                label="Any blockers or issues?",
                enabled=True,
                is_blocker_type=True,
                created_at=now - timedelta(days=31),
            )
            session.add_all([q_yesterday, q_today_q, q_blockers])
            await session.flush()

            # ── 5. Create Checkins + CheckinAnswers (30 days) ─────────────────
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

                    # Yesterday
                    session.add(CheckinAnswer(
                        checkin_id=checkin.id,
                        question_id=q_yesterday.id,
                        answer=phrase_y,
                        created_at=submitted_time,
                    ))
                    # Today
                    session.add(CheckinAnswer(
                        checkin_id=checkin.id,
                        question_id=q_today_q.id,
                        answer=phrase_t,
                        created_at=submitted_time,
                    ))
                    # Blocker question — empty for most days (no blocker created)
                    session.add(CheckinAnswer(
                        checkin_id=checkin.id,
                        question_id=q_blockers.id,
                        answer="",
                        created_at=submitted_time,
                    ))
                    checkin_count += 1

            await session.flush()

            # ── 6. Create standalone Blockers ────────────────────────────────
            alice, bob, carlos = members

            blockers_config = [
                {
                    "user": alice,
                    "assigned_to": None,
                    "title": "Deployment environment keeps resetting",
                    "description": (
                        "Every morning the staging server config resets to defaults. "
                        "Spending 30 min manually reconfiguring before real work can start. "
                        "Root cause unknown — likely a cron job overwriting the config."
                    ),
                    "status": "open",
                    "days_ago": 5,
                    "resolved_at": None,
                },
                {
                    "user": alice,
                    "assigned_to": bob,
                    "title": "API documentation missing for v2 endpoints",
                    "description": (
                        "No docs for the new v2 REST endpoints. Manually testing each "
                        "endpoint to reverse-engineer the request/response schema. "
                        "Blocking frontend integration work."
                    ),
                    "status": "open",
                    "days_ago": 10,
                    "resolved_at": None,
                },
                {
                    "user": bob,
                    "assigned_to": None,
                    "title": "Test environment setup fails on fresh machines",
                    "description": (
                        "New team members spend hours trying to set up the test environment. "
                        "Docker config has an unresolved dependency conflict with node 18. "
                        "Manually patching each time."
                    ),
                    "status": "acknowledged",
                    "days_ago": 15,
                    "resolved_at": None,
                },
                {
                    "user": carlos,
                    "assigned_to": None,
                    "title": "Client approval pending — integration cannot proceed",
                    "description": (
                        "Waiting on client sign-off on the revised wireframes. "
                        "Two weeks overdue. The entire integration sprint is blocked "
                        "until written approval is received."
                    ),
                    "status": "in_progress",
                    "days_ago": 20,
                    "resolved_at": None,
                },
                {
                    "user": bob,
                    "assigned_to": None,
                    "title": "SSL certificate expiry on staging server",
                    "description": (
                        "Staging SSL cert expired causing browser warnings for QA team. "
                        "Manually renewed via cPanel. Root cause: no automated renewal "
                        "pipeline (certbot or similar) configured."
                    ),
                    "status": "resolved",
                    "days_ago": 25,
                    "resolved_at": now - timedelta(days=20),
                },
            ]

            for bd in blockers_config:
                created = now - timedelta(days=bd["days_ago"])
                b = Blocker(
                    team_id=team.id,
                    user_id=bd["user"].id,
                    assigned_to=bd["assigned_to"].id if bd["assigned_to"] else None,
                    status=bd["status"],
                    title=bd["title"],
                    description=bd["description"],
                    created_at=created,
                    updated_at=created,
                    resolved_at=bd["resolved_at"],
                )
                session.add(b)

            await session.flush()

    # ── Print summary ─────────────────────────────────────────────────────────
    print()
    print("✅  Test data seeded successfully!")
    print()
    print(f"  Team    : {TEAM_NAME}  (Starter plan — active)")
    print(f"  Manager : {MANAGER_EMAIL}  →  password: {TEST_PASSWORD}")
    print()

    rates = [
        f"{c['hourly_rate']} {c['currency']}" if c["hourly_rate"] else "no rate set"
        for c in MEMBER_CONFIGS
    ]
    hrs_labels = [
        f"{c['hours_per_day']}h/day" if c["hours_per_day"] else "not set"
        for c in MEMBER_CONFIGS
    ]
    conf_labels = ["✓ confirmed" if c["hours_confirmed"] else "not confirmed" for c in MEMBER_CONFIGS]
    pct = ["~87%", "~67%", "~50%"]

    for i, cfg in enumerate(MEMBER_CONFIGS):
        print(
            f"  Member  : {cfg['email']}  →  {cfg['name']}"
            f"  |  rate: {rates[i]}  |  hours: {hrs_labels[i]} ({conf_labels[i]})"
            f"  |  check-in rate: {pct[i]}"
        )

    open_c = sum(1 for b in blockers_config if b["status"] == "open")
    ack_c  = sum(1 for b in blockers_config if b["status"] == "acknowledged")
    ip_c   = sum(1 for b in blockers_config if b["status"] == "in_progress")
    res_c  = sum(1 for b in blockers_config if b["status"] == "resolved")

    print()
    print(f"  Check-ins : {checkin_count} records over {SEED_DAYS} days")
    print(
        f"  Blockers  : {len(blockers_config)} total | "
        f"open: {open_c}, acknowledged: {ack_c}, in_progress: {ip_c}, resolved: {res_c}"
    )
    print()
    print("  Cost Intelligence : Alice ($48 missed cost) + Bob ($50.4 missed) + Carlos (no rate)")
    print("  Automation Radar  : Run analysis on the team to see repeated-task findings")
    print()
    print("  To remove all test data : python3 remove_test_data.py")
    print()

    await _engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
