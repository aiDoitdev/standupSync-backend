"""
Remove ALL seeded test data from StandupSync database.

Handles all seed scripts:
  - seed_test_data.py          → [TEST] Test Team Alpha  + testmember{1,2,3}@test.com
  - seed_large_test_data.py    → all [TEST] * teams       + *@testcorp.com members
  - seed_complete_test_data.py → [TEST] Alpha Engineering Team + *@standupsync.test

Deletion order (respects FK constraints):
  1. AutomationSchedule       — for each [TEST] team
  2. AutomationIntegration    — for each [TEST] team
  3. AutomationAnalysis       — for each [TEST] team
  4. BlockerResolutions        — for all blockers in [TEST] teams
  5. BlockerComments           — for all blockers in [TEST] teams
  6. Blockers                  — for all [TEST] teams
  7. CheckinAnswers            — for all check-ins in [TEST] teams
  8. Checkins                  — for all [TEST] teams
  9. TeamQuestions             — for all [TEST] teams
  10. TeamMembers              — for all [TEST] teams
  11. Teams                   — all [TEST] * teams
  12. @standupsync.test users  — all complete-seed accounts
  13. @testcorp.com users      — all large-seed member accounts
  14. Small-seed users         — testmember{1,2,3}@test.com
  15. Manager                  — testmanager@test.com (only if no other teams remain)

Run:
  python3 remove_test_data.py
"""

import asyncio

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
    AutomationAnalysis,
    AutomationSchedule,
    AutomationIntegration,
)

# ─── Test data identifiers (cover all seed scripts) ──────────────────────────

MANAGER_EMAIL        = "testmanager@test.com"
SMALL_SEED_EMAILS    = [
    "testmember1@test.com",
    "testmember2@test.com",
    "testmember3@test.com",
]
TEST_TEAM_PREFIX     = "[TEST]"           # all teams starting with this are test data
TESTCORP_DOMAIN      = "@testcorp.com"   # all large-seed member accounts
STANDUPSYNC_DOMAIN   = "@standupsync.test"  # all complete-seed accounts

# ─── DB engine (PgBouncer-compatible) ────────────────────────────────────────

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


async def _delete_team(team: Team, session: AsyncSession, counts: dict) -> None:
    """Delete all data for one [TEST] team in correct FK order."""
    team_id = team.id

    # AutomationSchedule
    sched_result = await session.execute(
        select(AutomationSchedule).where(AutomationSchedule.team_id == team_id)
    )
    scheds = sched_result.scalars().all()
    for s in scheds:
        await session.delete(s)
    counts["automation_schedules"] = counts.get("automation_schedules", 0) + len(scheds)
    await session.flush()

    # AutomationIntegration
    integ_result = await session.execute(
        select(AutomationIntegration).where(AutomationIntegration.team_id == team_id)
    )
    integs = integ_result.scalars().all()
    for i in integs:
        await session.delete(i)
    counts["automation_integrations"] = counts.get("automation_integrations", 0) + len(integs)
    await session.flush()

    # AutomationAnalyses
    result = await session.execute(
        select(AutomationAnalysis).where(AutomationAnalysis.team_id == team_id)
    )
    rows = result.scalars().all()
    for r in rows:
        await session.delete(r)
    counts["automation_analyses"] = counts.get("automation_analyses", 0) + len(rows)
    await session.flush()

    # Blockers (must delete comments + resolutions first)
    blk_result = await session.execute(
        select(Blocker).where(Blocker.team_id == team_id)
    )
    blockers = blk_result.scalars().all()

    res_count = 0
    cmt_count = 0
    for b in blockers:
        # BlockerResolutions
        res_result = await session.execute(
            select(BlockerResolution).where(BlockerResolution.blocker_id == b.id)
        )
        resolutions = res_result.scalars().all()
        for r in resolutions:
            await session.delete(r)
        res_count += len(resolutions)

        # BlockerComments
        cmt_result = await session.execute(
            select(BlockerComment).where(BlockerComment.blocker_id == b.id)
        )
        comments = cmt_result.scalars().all()
        for c in comments:
            await session.delete(c)
        cmt_count += len(comments)

    await session.flush()
    counts["blocker_resolutions"] = counts.get("blocker_resolutions", 0) + res_count
    counts["blocker_comments"] = counts.get("blocker_comments", 0) + cmt_count

    for b in blockers:
        await session.delete(b)
    counts["blockers"] = counts.get("blockers", 0) + len(blockers)
    await session.flush()

    # CheckinAnswers (via Checkins)
    checkin_result = await session.execute(
        select(Checkin).where(Checkin.team_id == team_id)
    )
    checkins = checkin_result.scalars().all()
    answer_count = 0
    for c in checkins:
        ans_result = await session.execute(
            select(CheckinAnswer).where(CheckinAnswer.checkin_id == c.id)
        )
        answers = ans_result.scalars().all()
        for a in answers:
            await session.delete(a)
        answer_count += len(answers)
    counts["checkin_answers"] = counts.get("checkin_answers", 0) + answer_count
    await session.flush()

    # Checkins
    for c in checkins:
        await session.delete(c)
    counts["checkins"] = counts.get("checkins", 0) + len(checkins)
    await session.flush()

    # TeamQuestions
    tq_result = await session.execute(
        select(TeamQuestion).where(TeamQuestion.team_id == team_id)
    )
    tqs = tq_result.scalars().all()
    for tq in tqs:
        await session.delete(tq)
    counts["team_questions"] = counts.get("team_questions", 0) + len(tqs)
    await session.flush()

    # TeamMembers
    tm_result = await session.execute(
        select(TeamMember).where(TeamMember.team_id == team_id)
    )
    tms = tm_result.scalars().all()
    for tm in tms:
        await session.delete(tm)
    counts["team_members"] = counts.get("team_members", 0) + len(tms)
    await session.flush()

    # Team itself
    await session.delete(team)
    counts["teams"] = counts.get("teams", 0) + 1
    await session.flush()


async def main() -> None:
    async with _Session() as session:
        async with session.begin():

            counts: dict[str, int] = {}

            # ── Find ALL test teams ────────────────────────────────────────────
            # Match teams by [TEST] name prefix OR teams owned by a test-domain manager
            all_users_result = await session.execute(select(User))
            all_users_init = all_users_result.scalars().all()
            test_manager_ids = {
                u.id for u in all_users_init
                if u.email.endswith(STANDUPSYNC_DOMAIN) and u.role == "manager"
            }

            all_teams_result = await session.execute(select(Team))
            all_teams = all_teams_result.scalars().all()
            test_teams = [
                t for t in all_teams
                if t.name.startswith(TEST_TEAM_PREFIX) or t.manager_id in test_manager_ids
            ]

            if not test_teams:
                print("ℹ️  No test teams found in the database.")
            else:
                print(f"\nFound {len(test_teams)} test team(s) to remove:")
                for t in test_teams:
                    print(f"  • {t.name}")
                print()

            # ── Delete each test team and its data ────────────────────────────
            for team in test_teams:
                await _delete_team(team, session, counts)

            # ── Delete @standupsync.test accounts (complete seed) ─────────────
            all_users_result = await session.execute(select(User))
            all_users = all_users_result.scalars().all()
            standupsync_users = [u for u in all_users if u.email.endswith(STANDUPSYNC_DOMAIN)]
            for u in standupsync_users:
                await session.delete(u)
            counts["standupsync_users"] = len(standupsync_users)
            await session.flush()

            # ── Delete @testcorp.com member accounts (large seed) ─────────────
            all_users_result2 = await session.execute(select(User))
            all_users2 = all_users_result2.scalars().all()
            testcorp_users = [u for u in all_users2 if u.email.endswith(TESTCORP_DOMAIN)]
            for u in testcorp_users:
                await session.delete(u)
            counts["testcorp_users"] = len(testcorp_users)
            await session.flush()

            # ── Delete small-seed member accounts ─────────────────────────────
            small_deleted = 0
            for email in SMALL_SEED_EMAILS:
                u_result = await session.execute(select(User).where(User.email == email))
                u = u_result.scalar_one_or_none()
                if u:
                    await session.delete(u)
                    small_deleted += 1
            counts["small_seed_users"] = small_deleted
            await session.flush()

            # ── Delete manager if no other teams remain ────────────────────────
            remaining_teams_result = await session.execute(select(Team))
            remaining_teams = remaining_teams_result.scalars().all()
            non_test_teams = [t for t in remaining_teams if not t.name.startswith(TEST_TEAM_PREFIX)]

            if not non_test_teams:
                mgr_result = await session.execute(
                    select(User).where(User.email == MANAGER_EMAIL)
                )
                mgr = mgr_result.scalar_one_or_none()
                if mgr:
                    await session.delete(mgr)
                    counts["manager_deleted"] = 1
                    await session.flush()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("✅  All test data removed successfully!")
    print()
    label_map = {
        "teams":                   "teams deleted",
        "team_members":            "team_member rows deleted",
        "team_questions":          "team_question rows deleted",
        "blocker_resolutions":     "blocker_resolution rows deleted",
        "blocker_comments":        "blocker_comment rows deleted",
        "blockers":                "blocker rows deleted",
        "checkin_answers":         "checkin_answer rows deleted",
        "checkins":                "checkin rows deleted",
        "automation_schedules":    "automation_schedule rows deleted",
        "automation_integrations": "automation_integration rows deleted",
        "automation_analyses":     "automation_analysis rows deleted",
        "standupsync_users":       "@standupsync.test user accounts deleted",
        "testcorp_users":          "@testcorp.com user accounts deleted",
        "small_seed_users":        "testmember@test.com accounts deleted",
        "manager_deleted":         "manager account deleted",
    }
    for key, label in label_map.items():
        n = counts.get(key, 0)
        if n:
            print(f"  {n:>6}  {label}")
    print()
    print("  Run `python3 seed_complete_test_data.py` to reseed.")
    print()

    await _engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
