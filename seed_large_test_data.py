"""
Large multi-team test data seed for StandupSync.

Creates 10 Starter-plan teams all owned by testmanager@test.com:
  1.  [TEST] Founder's Office     —  8 members
  2.  [TEST] Engineering          — 15 members
  3.  [TEST] Product & Design     — 12 members
  4.  [TEST] Marketing            — 12 members
  5.  [TEST] Sales                — 12 members
  6.  [TEST] HR & People          — 10 members
  7.  [TEST] Finance              —  8 members
  8.  [TEST] Operations           — 10 members
  9.  [TEST] Customer Success     — 12 members
  10. [TEST] Data & Analytics     — 10 members

Total: ~109 member accounts (@testcorp.com), 30 days of check-in history,
       realistic per-department task phrases, varied USD rates, mixed
       confirmed/unconfirmed hours, and 3-5 blockers per team.

Identified by: [TEST] prefix on team names, @testcorp.com on member emails.
Remove: python3 remove_test_data.py  (handles all [TEST] data)

Run:
  python3 seed_large_test_data.py
"""

import asyncio
import hashlib
import uuid
from datetime import datetime, date, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from database import DATABASE_URL
from models import User, Team, TeamMember, TeamQuestion, Checkin, CheckinAnswer, Blocker
from auth import hash_password

# ─── Config ───────────────────────────────────────────────────────────────────

MANAGER_EMAIL = "testmanager@test.com"
MANAGER_NAME  = "Test Manager"
TEST_PASSWORD = "Test@1234"
SEED_DAYS     = 30

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _should_submit(team_name: str, member_email: str, day_idx: int, rate: float) -> bool:
    """Deterministic pseudo-random submission using MD5 hash seeded per member+day."""
    key = f"{team_name}:{member_email}:{day_idx}"
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
    return (h % 1000) < int(rate * 1000)


def _submit_time(member_email: str, day_idx: int, target_date: date) -> datetime:
    """Return a realistic submission time spread across 8 AM – 5:30 PM."""
    h = int(hashlib.md5(f"time:{member_email}:{day_idx}".encode()).hexdigest()[:8], 16)
    minutes_from_midnight = 480 + (h % 570)   # 8:00 → 17:30
    hour, minute = divmod(minutes_from_midnight, 60)
    return datetime.combine(target_date, datetime.min.time()).replace(
        hour=hour, minute=minute, second=0
    )


# ─── Team data ────────────────────────────────────────────────────────────────
#
# members tuple: (name, email, hourly_rate_usd, hours_per_day, hours_confirmed, send_time)
# blockers dict keys: reporter, title, description, status, days_ago, [resolved_days_ago]

TEAMS = [

    # ── 1. Founder's Office ────────────────────────────────────────────────────
    {
        "name": "[TEST] Founder's Office",
        "team_type": "executive",
        "checkin_rate": 0.90,
        "questions": [
            ("What strategic initiative did you advance yesterday?",  False),
            ("What is your key focus today?",                         False),
            ("Any critical escalations or blockers?",                 True),
        ],
        "members": [
            ("Victoria Hartwell", "v.hartwell@testcorp.com", 200.0, 9.0,  True,  "08:00"),
            ("James Okafor",      "j.okafor@testcorp.com",   185.0, 8.0,  True,  "08:15"),
            ("Priya Patel",       "p.patel@testcorp.com",    195.0, 9.0,  True,  "07:30"),
            ("Marcus Chen",       "m.chen@testcorp.com",     175.0, 8.0,  True,  "08:00"),
            ("Sophie Laurent",    "s.laurent@testcorp.com",  165.0, 8.0,  True,  "08:30"),
            ("Daniel Reeves",     "d.reeves@testcorp.com",   180.0, 8.0,  True,  "08:00"),
            ("Amara Nwosu",       "a.nwosu@testcorp.com",    170.0, 8.0,  True,  "09:00"),
            ("Lucas Rivera",      "l.rivera@testcorp.com",   160.0, 8.0,  False, "09:00"),
        ],
        "yesterday_phrases": [
            "Reviewed Q2 OKR progress and manually compiled executive summary for board",
            "Met with investors and manually built performance deck from raw department data",
            "Drafted strategic partnership proposal for enterprise client from scratch",
            "Manually aggregated KPIs from all 8 department heads for the weekly board report",
            "Conducted one-on-one performance reviews with all direct reports today",
            "Prepared quarterly roadmap update and manually updated all presentation slides",
            "Reviewed and signed off on all pending budget approvals across departments",
            "Manually compiled company-wide metrics from teams for all-hands meeting deck",
            "Reviewed hiring pipeline and manually updated offer status tracker spreadsheet",
            "Led strategic planning session and manually captured all action items shared",
        ],
        "today_phrases": [
            "Manually compile weekly executive KPI report from all eight department sheets",
            "Prepare board meeting materials with manually aggregated team performance metrics",
            "Review all department weekly reports and manually highlight risks for leadership",
            "Draft investor update email with manually pulled financial and product metrics",
            "Manually consolidate monthly performance data into single unified board document",
            "Review hiring decisions and manually update candidate pipeline status tracker",
            "Compile company-wide OKR status by manually collecting from all team leads",
            "Prepare all-hands deck with manually gathered product and revenue data today",
            "Manually update strategy roadmap across all active company initiatives today",
            "Review partnership proposals and manually compile stakeholder feedback report",
        ],
        "blockers": [
            {
                "reporter": "v.hartwell@testcorp.com",
                "title": "No unified KPI dashboard — metrics manually gathered every week",
                "description": "Spending 4+ hours every Monday pulling metrics from 10 different team spreadsheets, reconciling data conflicts, and formatting for the board. Need a single source of truth.",
                "status": "open",
                "days_ago": 8,
            },
            {
                "reporter": "p.patel@testcorp.com",
                "title": "Board deck prep takes a full day due to manual data collection",
                "description": "Each quarter involves manually contacting every department head, collecting numbers, reconciling conflicts, and reformatting. Estimated 1.5 days of wasted effort per board cycle.",
                "status": "acknowledged",
                "days_ago": 15,
            },
            {
                "reporter": "j.okafor@testcorp.com",
                "title": "OKR tracker always 5 days stale — no automated status update",
                "description": "The OKR tracker is a Google Sheet updated manually each week. Leadership is making resource decisions on outdated data. Need real-time OKR visibility.",
                "status": "in_progress",
                "days_ago": 22,
            },
        ],
    },

    # ── 2. Engineering ─────────────────────────────────────────────────────────
    {
        "name": "[TEST] Engineering",
        "team_type": "software",
        "checkin_rate": 0.75,
        "questions": [
            ("What did you ship, fix, or review yesterday?",       False),
            ("What are you building or debugging today?",          False),
            ("Any technical blockers or external dependencies?",   True),
        ],
        "members": [
            ("Ethan Brooks",     "e.brooks@testcorp.com",    105.0, 8.0,  True,  "09:00"),
            ("Samira Khan",      "s.khan@testcorp.com",       98.0, 8.0,  True,  "09:00"),
            ("Noah Williams",    "n.williams@testcorp.com",  112.0, 8.0,  False, "09:15"),
            ("Aisha Osei",       "a.osei@testcorp.com",       88.0, 8.0,  False, "09:30"),
            ("Ryan Torres",      "r.torres@testcorp.com",     95.0, 8.0,  True,  "08:45"),
            ("Mei Lin",          "m.lin@testcorp.com",        92.0, 8.0,  False, "09:00"),
            ("Arjun Sharma",     "a.sharma@testcorp.com",    110.0, 8.0,  True,  "09:00"),
            ("Olivia Scott",     "o.scott@testcorp.com",      85.0, 8.0,  False, "10:00"),
            ("David Park",       "d.park@testcorp.com",      118.0, 8.0,  True,  "08:30"),
            ("Fatima Al-Hassan", "f.alhassan@testcorp.com",   90.0, 8.0,  False, "09:30"),
            ("Jack Murphy",      "j.murphy@testcorp.com",     78.0, 8.0,  False, "10:00"),
            ("Zoe Anderson",     "z.anderson@testcorp.com",  105.0, 8.0,  True,  "09:00"),
            ("Ravi Nair",        "r.nair@testcorp.com",      115.0, 8.0,  False, "09:15"),
            ("Chloe Martin",     "c.martin@testcorp.com",     82.0, 8.0,  None,  "10:00"),
            ("Ivan Petrov",      "i.petrov@testcorp.com",    108.0, 8.0,  True,  "08:45"),
        ],
        "yesterday_phrases": [
            "Pushed hotfix to production after manually running full regression test suite",
            "Updated CI deployment pipeline scripts after overnight environment config reset",
            "Ran manual end-to-end API test suite across all endpoints before release build",
            "Manually updated all open JIRA tickets and sprint board statuses after standup",
            "Resolved merge conflicts and manually updated the deployment CHANGELOG file",
            "Debugged staging environment configuration issues caused by nightly auto-reset",
            "Manually generated and distributed weekly build status report to product team",
            "Manually synced and ran all database migration scripts to staging environment",
            "Reviewed open pull requests and manually triggered CI pipeline for all merges",
            "Manually ran performance benchmark tests and documented results in spreadsheet",
            "Fixed failing unit tests and manually updated test runner configuration file",
            "Manually updated all API documentation after latest endpoint schema changes",
        ],
        "today_phrases": [
            "Run full manual regression test suite before shipping the release build today",
            "Manually update all sprint JIRA ticket statuses after morning standup today",
            "Update all deployment scripts and manually push latest build to staging server",
            "Manual code review of all open pull requests before end-of-day merge window",
            "Debug staging environment config issues caused by last nights auto-reset script",
            "Manually generate weekly engineering status report for all product stakeholders",
            "Update and manually sync API documentation with all latest endpoint changes",
            "Run manual performance tests on the checkout flow and log all results today",
            "Update CHANGELOG and manually bump version number before next release",
            "Manually trigger CI pipeline and monitor build output logs for all branches",
            "Fix all failing integration tests and manually update test environment config",
            "Run manual smoke tests on the new payment integration flow before shipping",
        ],
        "blockers": [
            {
                "reporter": "e.brooks@testcorp.com",
                "title": "Staging config resets every night — 30 min manual fix every morning",
                "description": "A cron job overwrites the staging environment config nightly. Each morning 2-3 engineers spend 30 minutes manually reconfiguring before productive work can begin.",
                "status": "open",
                "days_ago": 6,
            },
            {
                "reporter": "d.park@testcorp.com",
                "title": "No CI pipeline for mobile apps repository",
                "description": "Mobile engineers run builds and tests manually before every deployment. No CI integration exists. Untested code has reached production twice in the last sprint.",
                "status": "open",
                "days_ago": 12,
            },
            {
                "reporter": "r.nair@testcorp.com",
                "title": "3rd party API rate limits breaking automated integration tests",
                "description": "Payment API rate limits break our test suite when run in parallel. Currently manually throttling tests by adding delays - adds 45 mins per test run.",
                "status": "acknowledged",
                "days_ago": 18,
            },
            {
                "reporter": "a.sharma@testcorp.com",
                "title": "Database migration scripts fail silently in production",
                "description": "Last Tuesday's migration appeared successful but 3 tables were not updated. Discovered 24 hours later from user bug reports. Manual rollback took 2 hours.",
                "status": "in_progress",
                "days_ago": 9,
            },
            {
                "reporter": "s.khan@testcorp.com",
                "title": "SSL certificates expiring on 3 microservices within 7 days",
                "description": "No automated certificate renewal configured. Manually renewed certs each year via cPanel. 3 certs expire this Sunday — risk of service outage.",
                "status": "resolved",
                "days_ago": 24,
                "resolved_days_ago": 20,
            },
        ],
    },

    # ── 3. Product & Design ────────────────────────────────────────────────────
    {
        "name": "[TEST] Product & Design",
        "team_type": "product",
        "checkin_rate": 0.82,
        "questions": [
            ("What did you deliver, design, or scope yesterday?",     False),
            ("What are you designing, reviewing, or speccing today?", False),
            ("Any blockers or pending stakeholder approvals?",        True),
        ],
        "members": [
            ("Emma Johnson",    "e.johnson@testcorp.com",  92.0,  8.0,  True,  "09:00"),
            ("Tyler Nguyen",    "t.nguyen@testcorp.com",   85.0,  8.0,  True,  "09:00"),
            ("Kavya Reddy",     "k.reddy@testcorp.com",    90.0,  8.0,  False, "09:15"),
            ("Leo Fontaine",    "l.fontaine@testcorp.com", 78.0,  8.0,  False, "09:30"),
            ("Nina Kovacs",     "n.kovacs@testcorp.com",   82.0,  8.0,  True,  "09:00"),
            ("Tom Bradley",     "t.bradley@testcorp.com",  88.0,  8.0,  False, "10:00"),
            ("Sana Hussain",    "s.hussain@testcorp.com",  75.0,  8.0,  True,  "09:30"),
            ("Max Werner",      "m.werner@testcorp.com",   80.0,  8.0,  False, "09:00"),
            ("Isabelle Dupont", "i.dupont@testcorp.com",   95.0,  8.0,  True,  "08:45"),
            ("Raj Mehta",       "r.mehta@testcorp.com",    None,  8.0,  False, "09:00"),
            ("Alex Kim",        "a.kim@testcorp.com",      88.0,  8.0,  False, "09:15"),
            ("Leila Ahmadi",    "l.ahmadi@testcorp.com",   None,  8.0,  False, "10:00"),
        ],
        "yesterday_phrases": [
            "Manually updated product roadmap slides and shared updated version with engineering",
            "Compiled and sent weekly sprint review report to all cross-functional stakeholders",
            "Manually synced all updated design specs from Figma into engineering JIRA tickets",
            "Ran user research session and manually compiled all interview findings into report",
            "Updated wireframes post-feedback and manually notified dev team of all spec changes",
            "Manually created complete design handoff documentation for three features shipped",
            "Compiled roadmap prioritisation report from manually gathered customer feedback sources",
            "Updated and manually distributed weekly product changelog to all department leads",
            "Manual QA on mobile app screens before release candidate sign-off meeting",
            "Gathered stakeholder inputs from 6 sources and manually merged into one spec doc",
        ],
        "today_phrases": [
            "Manually update product roadmap with latest priority changes from leadership team",
            "Compile sprint review deck with manually gathered engineering delivery statistics",
            "Manually sync all open design tickets from Figma into engineering JIRA today",
            "Update design handoff documentation and manually notify all assigned engineers",
            "Run manual QA pass on all new screens in the upcoming release candidate build",
            "Gather customer feedback from multiple channels and manually compile weekly report",
            "Manually update product changelog and distribute to all team and department leads",
            "Write feature spec and manually get written sign-off from five key stakeholders",
            "Manually collect all UX test results and compile into weekly product insight report",
            "Update roadmap prioritisation document with manually reconciled PM and sales feedback",
        ],
        "blockers": [
            {
                "reporter": "e.johnson@testcorp.com",
                "title": "Design-to-engineering handoff has no automated workflow",
                "description": "Every Figma design update requires manually creating or updating 5-10 JIRA tickets with screenshots, specs, notes, and acceptance criteria. Takes 1-2 hours per feature.",
                "status": "open",
                "days_ago": 10,
            },
            {
                "reporter": "i.dupont@testcorp.com",
                "title": "No shared design system — components recreated manually per project",
                "description": "Designers check 3 different Figma files to find the correct component version. Causes inconsistency and doubles design review time before engineering handoff.",
                "status": "in_progress",
                "days_ago": 20,
            },
            {
                "reporter": "k.reddy@testcorp.com",
                "title": "Customer feedback fragmented across 5 channels — manual consolidation weekly",
                "description": "Feedback from email, Slack, surveys, app store reviews, and support tickets must be manually merged each week. Takes 3 hours to produce a coherent insight report.",
                "status": "acknowledged",
                "days_ago": 7,
            },
        ],
    },

    # ── 4. Marketing ───────────────────────────────────────────────────────────
    {
        "name": "[TEST] Marketing",
        "team_type": "marketing",
        "checkin_rate": 0.72,
        "questions": [
            ("What campaign or content work did you complete yesterday?", False),
            ("What is your marketing or content focus today?",            False),
            ("Any blockers, budget holds, or approval delays?",           True),
        ],
        "members": [
            ("Hannah Clarke",    "h.clarke@testcorp.com",    65.0,  8.0,  True,  "09:00"),
            ("Chris Taylor",     "c.taylor@testcorp.com",    60.0,  8.0,  False, "09:15"),
            ("Divya Menon",      "d.menon@testcorp.com",     62.0,  8.0,  True,  "09:00"),
            ("Finn O'Brien",     "f.obrien@testcorp.com",    55.0,  8.0,  False, "09:30"),
            ("Zara Mitchell",    "z.mitchell@testcorp.com",  68.0,  8.0,  True,  "08:45"),
            ("Luca Rossi",       "l.rossi@testcorp.com",     58.0,  8.0,  False, "09:00"),
            ("Aisha Kamara",     "a.kamara@testcorp.com",    None,  8.0,  False, "10:00"),
            ("Ben Foster",       "b.foster@testcorp.com",    62.0,  8.0,  False, "09:30"),
            ("Elena Vasiliev",   "e.vasiliev@testcorp.com",  70.0,  8.0,  True,  "09:00"),
            ("Oscar Lindqvist",  "o.lindqvist@testcorp.com", 55.0,  8.0,  False, "10:00"),
            ("Mia Thompson",     "m.thompson@testcorp.com",  None,  8.0,  False, "09:30"),
            ("Shan Wei",         "s.wei@testcorp.com",       60.0,  8.0,  False, "09:00"),
        ],
        "yesterday_phrases": [
            "Manually pulled campaign performance data from Google Ads and Meta Ads into sheet",
            "Compiled and sent weekly marketing performance report to all leadership stakeholders",
            "Manually scheduled all social media posts across LinkedIn, Instagram, and Twitter",
            "Exported and merged email metrics from Mailchimp and HubSpot dashboards manually",
            "Updated SEO keyword ranking tracker spreadsheet with the latest weekly data",
            "Manually created and sent the weekly marketing email newsletter to subscriber list",
            "Compiled lead generation metrics from multiple platforms and updated tracking sheet",
            "Manually updated the content calendar with all scheduled posts for upcoming two weeks",
            "Pulled monthly attribution data from all paid channels and built report deck manually",
            "Manually exported and merged all paid ad performance metrics for optimisation review",
        ],
        "today_phrases": [
            "Manually pull and compile weekly campaign performance report from all platform sources",
            "Export all ad metrics from Google and Meta Ads manually and update tracking sheet",
            "Manually schedule all social media content posts for the week across all channels",
            "Pull email campaign data from Mailchimp and HubSpot manually for analytics update",
            "Update SEO keyword tracker manually with this weeks latest Google Search Console data",
            "Compile this weeks newsletter content and manually send to entire subscriber list",
            "Manually export and merge lead generation data from HubSpot for weekly review",
            "Manually update content calendar with all approved and pending posts for next month",
            "Compile monthly channel attribution report manually for upcoming executive presentation",
            "Pull all paid ad performance metrics and manually build weekly optimisation report",
        ],
        "blockers": [
            {
                "reporter": "h.clarke@testcorp.com",
                "title": "No unified marketing dashboard — 6 platform logins every Monday",
                "description": "Every Monday: log into Google Analytics, Meta Ads, Google Ads, Mailchimp, HubSpot, and LinkedIn. Export CSVs. Merge in Google Sheets. 3-4 hours of avoidable work weekly.",
                "status": "open",
                "days_ago": 11,
            },
            {
                "reporter": "e.vasiliev@testcorp.com",
                "title": "Email platform and CRM not integrated — manual subscriber sync required",
                "description": "Mailchimp subscriber list and HubSpot contacts are not synced. After every campaign, manually export/import updated lists. Risk of emailing unsubscribed contacts.",
                "status": "in_progress",
                "days_ago": 17,
            },
            {
                "reporter": "z.mitchell@testcorp.com",
                "title": "No social scheduling tool — every post published manually per platform",
                "description": "All content is manually posted to each social channel separately. No approval workflow exists. Posting errors and schedule misses happen at least 3x per week.",
                "status": "acknowledged",
                "days_ago": 25,
            },
        ],
    },

    # ── 5. Sales ───────────────────────────────────────────────────────────────
    {
        "name": "[TEST] Sales",
        "team_type": "sales",
        "checkin_rate": 0.80,
        "questions": [
            ("What deals, calls, or outreach did you complete yesterday?",     False),
            ("What pipeline activities or accounts are you working on today?", False),
            ("Any blockers stopping a deal from moving forward?",              True),
        ],
        "members": [
            ("Carlos Mendez",    "c.mendez@testcorp.com",    80.0,  8.0,  True,  "08:30"),
            ("Rachel Green",     "r.green@testcorp.com",     75.0,  8.0,  True,  "08:45"),
            ("Akira Yamamoto",   "a.yamamoto@testcorp.com",  82.0,  8.0,  True,  "09:00"),
            ("Daniel O'Connor",  "d.oconnor@testcorp.com",   70.0,  8.0,  False, "09:15"),
            ("Blessing Okonkwo", "b.okonkwo@testcorp.com",   78.0,  8.0,  False, "09:00"),
            ("Sofia Andersson",  "s.andersson@testcorp.com", 85.0,  8.0,  True,  "08:30"),
            ("Mike Harrison",    "m.harrison@testcorp.com",  None,  8.0,  False, "09:30"),
            ("Preti Kapoor",     "p.kapoor@testcorp.com",    72.0,  8.0,  False, "09:00"),
            ("Julian Meyer",     "j.meyer@testcorp.com",     88.0,  8.0,  True,  "08:45"),
            ("Tia Campbell",     "t.campbell@testcorp.com",  68.0,  8.0,  False, "09:30"),
            ("Vikas Gupta",      "v.gupta@testcorp.com",     None,  8.0,  False, "10:00"),
            ("Nora Walsh",       "n.walsh@testcorp.com",     75.0,  8.0,  False, "09:00"),
        ],
        "yesterday_phrases": [
            "Manually updated all CRM records with notes from completed discovery calls",
            "Compiled and distributed weekly sales pipeline report to leadership manually",
            "Manually sent 40 personalised follow-up emails to leads from last weeks conference",
            "Updated all deal stages in Salesforce manually after completing each prospect call",
            "Manually compiled the monthly win-loss analysis report for Q2 strategy review",
            "Called 12 prospects and manually logged all call notes into CRM after each call",
            "Manually exported pipeline data and built revenue forecast presentation for board",
            "Sent personalised outreach to 30 new leads and manually tracked all open rates",
            "Updated all proposal documents manually and sent to 5 enterprise account contacts",
            "Manually reconciled Q2 commission calculations and sent updated sheet to finance",
        ],
        "today_phrases": [
            "Manually update all CRM records with notes from all calls completed yesterday",
            "Compile and distribute weekly pipeline report with manually gathered deal stage data",
            "Manually send follow-up emails to all leads contacted within the last 7 days",
            "Update all deal stages manually in Salesforce after completing morning call block",
            "Manually compile monthly win-loss analysis report for quarterly executive review",
            "Call all high-priority prospects and manually log every call note into the CRM",
            "Manually export pipeline data and update quarterly revenue forecast presentation",
            "Personalised outreach to 25 new leads and manually track all response rates today",
            "Update all enterprise proposal documents manually and send to 4 new accounts today",
            "Manually calculate all rep commission totals and send updated tracker to finance",
        ],
        "blockers": [
            {
                "reporter": "c.mendez@testcorp.com",
                "title": "CRM has 2,000+ duplicate lead records from CSV import last month",
                "description": "Conference lead import created thousands of duplicates. Manually deduplicating. Estimated 15 hours of remediation work. Sales team is calling the same prospects twice.",
                "status": "open",
                "days_ago": 9,
            },
            {
                "reporter": "j.meyer@testcorp.com",
                "title": "E-signature tool broken for EU-based clients — 3 deals delayed",
                "description": "DocuSign GDPR compliance flagging has broken e-sign for EU clients. Sending PDFs manually, printing, scanning, returning. 3 deals delayed by 1-2 weeks each.",
                "status": "open",
                "days_ago": 14,
            },
            {
                "reporter": "s.andersson@testcorp.com",
                "title": "Website leads not syncing to CRM — Zapier integration broken",
                "description": "Zapier integration between the contact form and Salesforce stopped working 3 weeks ago. Team checking and manually entering from a shared inbox to avoid losing leads.",
                "status": "in_progress",
                "days_ago": 21,
            },
            {
                "reporter": "r.green@testcorp.com",
                "title": "No automated follow-up sequences — all cold outreach done manually",
                "description": "After first contact, every follow-up email is sent manually. No sequences configured. High rate of leads going cold due to missed follow-up timing.",
                "status": "acknowledged",
                "days_ago": 28,
            },
        ],
    },

    # ── 6. HR & People ─────────────────────────────────────────────────────────
    {
        "name": "[TEST] HR & People",
        "team_type": "hr",
        "checkin_rate": 0.88,
        "questions": [
            ("What HR task or people operation did you complete yesterday?", False),
            ("What is your HR or recruitment focus today?",                 False),
            ("Any compliance issues or blockers to escalate?",              True),
        ],
        "members": [
            ("Ingrid Larsen",    "i.larsen@testcorp.com",    65.0,  8.0,  True,  "09:00"),
            ("Samuel Obi",       "s.obi@testcorp.com",       60.0,  8.0,  True,  "09:00"),
            ("Claire Dubois",    "c.dubois@testcorp.com",    68.0,  8.0,  True,  "08:45"),
            ("Kevin Huang",      "k.huang@testcorp.com",     55.0,  8.0,  False, "09:15"),
            ("Fatou Diallo",     "f.diallo@testcorp.com",    62.0,  8.0,  True,  "09:00"),
            ("Andrew Patterson", "a.patterson@testcorp.com", 70.0,  8.0,  False, "09:30"),
            ("Maria Santos",     "m.santos@testcorp.com",    None,  8.0,  False, "09:00"),
            ("Hiro Tanaka",      "h.tanaka@testcorp.com",    58.0,  8.0,  False, "10:00"),
            ("Naomi Bruce",      "n.bruce@testcorp.com",     64.0,  8.0,  True,  "09:00"),
            ("Paulo Fernandes",  "p.fernandes@testcorp.com", None,  8.0,  False, "09:30"),
        ],
        "yesterday_phrases": [
            "Manually processed and verified all timesheet submissions for upcoming payroll run",
            "Compiled monthly headcount report by manually pulling data from 3 separate HR systems",
            "Updated employee onboarding checklist and manually emailed tasks to 2 new joiners",
            "Manually reconciled all employee leave balances against HR system and payroll data",
            "Reviewed all pending job applications and manually updated the hiring pipeline tracker",
            "Manually compiled employee satisfaction survey results for leadership presentation",
            "Updated all HR policy documents manually and distributed to all department managers",
            "Manually coordinated and blocked time for interviews across 4 hiring managers calendars",
            "Processed all pending expense claims manually and sent approved list to finance team",
            "Updated the org chart manually to reflect all recent hires and role changes",
        ],
        "today_phrases": [
            "Manually process all timesheet submissions and verify against this periods payroll",
            "Compile monthly headcount report by manually pulling from all HR and payroll systems",
            "Update onboarding checklist manually and email all tasks to new joiners starting today",
            "Manually reconcile all leave balance discrepancies before the monthly payroll close",
            "Review all pending job applications and manually update the hiring pipeline tracker",
            "Manually compile the latest employee engagement survey results for leadership review",
            "Update all HR policy documents manually and distribute final version to all managers",
            "Manually coordinate all open interview slots across the four active hiring managers",
            "Process all pending expense claim approvals and manually update the finance tracker",
            "Update the org chart manually with all hires, exits, and promotions from this month",
        ],
        "blockers": [
            {
                "reporter": "i.larsen@testcorp.com",
                "title": "Timesheet process is fully manual — zero integration with payroll",
                "description": "Every fortnight employees email timesheets. HR manually verifies, aggregates, and re-enters into payroll software. 6+ hours per pay cycle. Errors found post-payment.",
                "status": "open",
                "days_ago": 13,
            },
            {
                "reporter": "c.dubois@testcorp.com",
                "title": "New joiner onboarding triggered by manual email chains to 7 people",
                "description": "Onboarding requires manually emailing IT, Finance, Team Lead, Facilities, Legal, Security, and HR Ops. Steps get missed frequently. No workflow or checklist automation.",
                "status": "in_progress",
                "days_ago": 18,
            },
            {
                "reporter": "s.obi@testcorp.com",
                "title": "Leave requests via email — no centralised leave management system",
                "description": "Leave requests emailed to managers, then forwarded to HR. No central tracker. Reconciliation before payroll takes 3+ hours. Conflicts discovered too late to fix.",
                "status": "resolved",
                "days_ago": 25,
                "resolved_days_ago": 20,
            },
        ],
    },

    # ── 7. Finance ─────────────────────────────────────────────────────────────
    {
        "name": "[TEST] Finance",
        "team_type": "finance",
        "checkin_rate": 0.92,
        "questions": [
            ("What financial task or close activity did you complete yesterday?", False),
            ("What are you reconciling, processing, or reviewing today?",         False),
            ("Any blockers, compliance holds, or approvals needed?",              True),
        ],
        "members": [
            ("Helen Knight",    "h.knight@testcorp.com",    90.0,  8.0,  True,  "08:30"),
            ("Robert Svensson", "r.svensson@testcorp.com",  85.0,  8.0,  True,  "08:45"),
            ("Adaeze Nwofor",   "a.nwofor@testcorp.com",    78.0,  8.0,  True,  "09:00"),
            ("William Marsh",   "w.marsh@testcorp.com",     92.0,  8.0,  True,  "08:30"),
            ("Yuki Sato",       "y.sato@testcorp.com",      80.0,  8.0,  True,  "09:00"),
            ("Philippe Moreau", "p.moreau@testcorp.com",    88.0,  8.0,  False, "09:00"),
            ("Donna McCarthy",  "d.mccarthy@testcorp.com",  82.0,  8.0,  True,  "08:45"),
            ("Aryan Khanna",    "a.khanna@testcorp.com",    None,  8.0,  False, "09:15"),
        ],
        "yesterday_phrases": [
            "Manually reconciled all accounts payable entries against this weeks bank statement",
            "Compiled and sent monthly budget variance report built from multiple spreadsheets",
            "Manually matched every expense receipt against the corporate credit card statement",
            "Manually updated monthly P&L by pulling figures from 4 different accounting views",
            "Manually verified all payroll figures before submitting to CEO for final sign-off",
            "Reconciled all outstanding vendor invoices manually and flagged mismatches to AP team",
            "Manually compiled quarterly tax provision workings across all company subsidiaries",
            "Updated the cash flow forecast manually using latest bank statement and AR data",
        ],
        "today_phrases": [
            "Manually reconcile all AP entries against this weeks incoming bank statement today",
            "Compile the monthly budget variance report by manually pulling from all data sources",
            "Manually match all expense receipts against credit card statement for month-end close",
            "Manually update monthly P&L by querying and pulling data from accounting system",
            "Manually verify all payroll entries and prepare sign-off summary for leadership",
            "Reconcile all outstanding vendor invoices manually before this weeks payment run",
            "Manually compile quarterly tax provision workings and distribute to external auditors",
            "Update cash flow forecast manually using latest accounts receivable and collections data",
        ],
        "blockers": [
            {
                "reporter": "h.knight@testcorp.com",
                "title": "Month-end close requires 3 full days of manual reconciliation work",
                "description": "No ERP integration. Team manually pulls data from Xero, payroll, expense tool, and bank portal. Reconciliation errors are often caught only at audit stage.",
                "status": "open",
                "days_ago": 6,
            },
            {
                "reporter": "w.marsh@testcorp.com",
                "title": "Expense management fully manual — no automated approval workflow",
                "description": "Employees email receipt attachments to finance inbox. Team manually downloads, categorises, and re-enters into accounting software. 8+ hours per accountant per month.",
                "status": "acknowledged",
                "days_ago": 19,
            },
        ],
    },

    # ── 8. Operations ──────────────────────────────────────────────────────────
    {
        "name": "[TEST] Operations",
        "team_type": "operations",
        "checkin_rate": 0.76,
        "questions": [
            ("What operations tasks or vendor coordination did you handle yesterday?", False),
            ("What are you managing, procuring, or coordinating today?",              False),
            ("Any vendor issues, facility blockers, or supply problems?",             True),
        ],
        "members": [
            ("Sean McDonald",   "s.mcdonald@testcorp.com",  58.0,  8.0,  True,  "08:45"),
            ("Alinta Watson",   "a.watson@testcorp.com",    55.0,  8.0,  True,  "09:00"),
            ("Rahim Chowdhury", "r.chowdhury@testcorp.com", 62.0,  8.0,  False, "09:15"),
            ("Luiza Barbosa",   "l.barbosa@testcorp.com",   50.0,  8.0,  False, "09:30"),
            ("Connor Walsh",    "c.walsh@testcorp.com",     60.0,  8.0,  True,  "09:00"),
            ("Mei Chen",        "mei.chen@testcorp.com",    None,  8.0,  False, "10:00"),
            ("Patrick Adeyemi", "p.adeyemi@testcorp.com",   55.0,  8.0,  False, "09:30"),
            ("Sandra Keller",   "s.keller@testcorp.com",    58.0,  8.0,  False, "09:00"),
            ("Oren Shapiro",    "o.shapiro@testcorp.com",   None,  8.0,  False, "10:00"),
            ("Diana Popescu",   "d.popescu@testcorp.com",   52.0,  8.0,  True,  "09:00"),
        ],
        "yesterday_phrases": [
            "Manually updated the vendor contact tracker and sent all outstanding renewal reminders",
            "Compiled weekly operations status report by manually gathering updates from all leads",
            "Manually coordinated delivery schedules and confirmed ETAs with 3 logistics vendors",
            "Updated the facilities maintenance log manually after completing the site walkthrough",
            "Manually chased all overdue vendor invoices and updated the payment status tracker",
            "Compiled inventory counts manually and submitted reorder requests to all suppliers",
            "Manually coordinated IT equipment procurement for 5 new hires starting this week",
            "Updated office seating allocation spreadsheet manually for Q3 headcount changes",
            "Manually created and distributed the weekly vendor SLA performance summary report",
            "Reconciled all office supply purchases manually and updated the Q2 budget tracker",
        ],
        "today_phrases": [
            "Manually update vendor tracker and send all outstanding contract renewal reminders",
            "Compile weekly ops status report by manually gathering updates from all department leads",
            "Manually coordinate todays delivery schedule and confirm ETAs with all logistics partners",
            "Update facilities maintenance log manually after completing site inspection today",
            "Manually follow up on all overdue vendor invoices and update the payment log sheet",
            "Compile inventory count manually and raise reorder requests for all low-stock items",
            "Manually coordinate the IT equipment setup for all new hires joining this week",
            "Update office seating plan spreadsheet manually before Q3 headcount changes take effect",
            "Manually create and distribute the weekly vendor SLA performance report to leadership",
            "Reconcile all facilities expenses manually and update the quarterly budget tracker",
        ],
        "blockers": [
            {
                "reporter": "s.mcdonald@testcorp.com",
                "title": "45 vendors tracked entirely in a Google Sheet — two contracts auto-renewed",
                "description": "No vendor management system. Contract renewals, SLA reviews, and payment terms are manually maintained. Two contracts auto-renewed last month without review or approval.",
                "status": "open",
                "days_ago": 10,
            },
            {
                "reporter": "a.watson@testcorp.com",
                "title": "IT equipment procurement has no structured request workflow",
                "description": "New hire equipment requests arrive via Slack or email. No formal approval flow. Items arrive late or wrong spec regularly. Status tracked manually in a spreadsheet.",
                "status": "in_progress",
                "days_ago": 16,
            },
            {
                "reporter": "c.walsh@testcorp.com",
                "title": "Office supply stockouts not caught until employees complain",
                "description": "No minimum stock alerts or automated reorder triggers. Stockouts discovered reactively. Orders placed manually each time. Last month: 3 stockouts in one week.",
                "status": "resolved",
                "days_ago": 22,
                "resolved_days_ago": 17,
            },
        ],
    },

    # ── 9. Customer Success ────────────────────────────────────────────────────
    {
        "name": "[TEST] Customer Success",
        "team_type": "customer_success",
        "checkin_rate": 0.83,
        "questions": [
            ("What customer interactions or escalations did you handle yesterday?",  False),
            ("What accounts or renewals are you focusing on today?",                 False),
            ("Any at-risk accounts or blockers to customer resolution?",             True),
        ],
        "members": [
            ("Grace Osei",     "g.osei@testcorp.com",      55.0,  8.0,  True,  "08:45"),
            ("Will Richards",  "w.richards@testcorp.com",  52.0,  8.0,  True,  "09:00"),
            ("Nadia Petrov",   "n.petrov@testcorp.com",    58.0,  8.0,  False, "09:15"),
            ("Tom Walters",    "t.walters@testcorp.com",   50.0,  8.0,  False, "09:30"),
            ("Keita Suzuki",   "k.suzuki@testcorp.com",    55.0,  8.0,  True,  "09:00"),
            ("Ayesha Mirza",   "a.mirza@testcorp.com",     60.0,  8.0,  False, "09:00"),
            ("Brent Kowalski", "b.kowalski@testcorp.com",  None,  8.0,  False, "10:00"),
            ("Funmi Adeyeba",  "f.adeyeba@testcorp.com",   52.0,  8.0,  False, "09:30"),
            ("Adam Schulz",    "a.schulz@testcorp.com",    58.0,  8.0,  True,  "09:00"),
            ("Lisa Monroe",    "l.monroe@testcorp.com",    None,  8.0,  False, "09:45"),
            ("Yusuf Ibrahim",  "y.ibrahim@testcorp.com",   55.0,  8.0,  False, "09:00"),
            ("Stella Park",    "s.park@testcorp.com",      50.0,  8.0,  True,  "09:15"),
        ],
        "yesterday_phrases": [
            "Manually compiled and categorised all NPS survey responses received this week",
            "Updated customer health scores manually across all 80 enterprise account records",
            "Manually tracked and updated all open support ticket statuses for key at-risk accounts",
            "Compiled the renewal pipeline report manually and sent to sales and leadership team",
            "Manually pulled product usage data from 3 dashboards to prepare for upcoming QBR",
            "Updated the customer success playbook and manually notified all CSMs of new changes",
            "Manually created and sent the monthly customer health summary to all account owners",
            "Compiled the churn risk report manually by reviewing all low-engagement account data",
            "Manually tracked all overdue feature requests and personally escalated 3 to product team",
            "Updated customer onboarding progress tracker manually for 4 accounts currently active",
        ],
        "today_phrases": [
            "Manually compile and categorise all NPS feedback responses received this week",
            "Update customer health scores manually across the full enterprise account portfolio",
            "Manually check and update all open support ticket statuses for priority accounts today",
            "Compile the weekly renewal pipeline report manually and share with sales team today",
            "Manually pull product usage data from all dashboards for this weeks QBR presentations",
            "Update CS playbook and manually distribute the latest version to the entire CS team",
            "Manually create and send the monthly health score summary to all assigned account owners",
            "Compile at-risk account report manually by reviewing all low-activity account data",
            "Manually track and escalate all overdue feature requests into the product backlog today",
            "Update customer onboarding tracker manually for all new accounts starting this month",
        ],
        "blockers": [
            {
                "reporter": "g.osei@testcorp.com",
                "title": "No automated health scoring — 80 accounts manually reviewed every Monday",
                "description": "Customer health scores maintained in a Google Sheet. CSM manually checks product usage, support tickets, and NPS for 80+ accounts. Takes 4-5 hours every single Monday.",
                "status": "open",
                "days_ago": 8,
            },
            {
                "reporter": "k.suzuki@testcorp.com",
                "title": "Renewal reminders sent manually — 3 accounts nearly churned this quarter",
                "description": "No automated renewal reminder system. CSMs manually remember renewal dates. Three accounts renewed late this quarter because a reminder was missed.",
                "status": "in_progress",
                "days_ago": 14,
            },
            {
                "reporter": "a.schulz@testcorp.com",
                "title": "QBR preparation takes 6 hours of manual data pulling per account",
                "description": "For each QBR: manually pull usage from Mixpanel, support data from Zendesk, and NPS from Delighted. No automated QBR prep. CSMs spending full days on data collection.",
                "status": "acknowledged",
                "days_ago": 20,
            },
        ],
    },

    # ── 10. Data & Analytics ───────────────────────────────────────────────────
    {
        "name": "[TEST] Data & Analytics",
        "team_type": "data",
        "checkin_rate": 0.78,
        "questions": [
            ("What data work, model, or analysis did you complete yesterday?",      False),
            ("What are you querying, modelling, or presenting today?",              False),
            ("Any data access issues, pipeline failures, or blockers?",             True),
        ],
        "members": [
            ("Hugo Zimmermann", "h.zimmermann@testcorp.com", 105.0, 8.0,  True,  "09:00"),
            ("Preeti Singh",    "p.singh@testcorp.com",       98.0, 8.0,  True,  "09:00"),
            ("Nathan Blake",    "n.blake@testcorp.com",      102.0, 8.0,  False, "09:15"),
            ("Soo-Yeon Kim",    "s.kim@testcorp.com",         95.0, 8.0,  True,  "09:00"),
            ("Emre Demir",      "e.demir@testcorp.com",      108.0, 8.0,  False, "09:30"),
            ("Chiara Ferrari",  "c.ferrari@testcorp.com",     92.0, 8.0,  True,  "09:00"),
            ("Liam O'Sullivan", "l.osullivan@testcorp.com",  100.0, 8.0,  False, "09:15"),
            ("Xinyi Zhang",     "x.zhang@testcorp.com",       None, 8.0,  False, "10:00"),
            ("Kofi Asante",     "k.asante@testcorp.com",      96.0, 8.0,  True,  "09:00"),
            ("Vera Marchetti",  "v.marchetti@testcorp.com",   None, 8.0,  False, "09:30"),
        ],
        "yesterday_phrases": [
            "Manually ran and distributed weekly analytics report to all department stakeholders",
            "Wrote and executed multiple manual SQL queries to answer this weeks ad-hoc requests",
            "Manually pulled data from 4 sources and merged in Python for the quarterly report",
            "Updated the analytics tracking plan and manually QA-verified all event properties",
            "Manually rebuilt the broken data pipeline and documented the fix in the runbook",
            "Manually compiled executive dashboard data from multiple source systems for review",
            "Wrote manual data extraction scripts to pull this months revenue cohort dataset",
            "Updated and manually refreshed all Tableau workbooks for the weekly reporting pack",
            "Manually ran data quality checks across all tables in the production database",
            "Built an ad-hoc report by manually joining 3 disparate datasets for leadership",
        ],
        "today_phrases": [
            "Manually run and distribute this weeks analytics report to all department stakeholders",
            "Write and execute manual SQL queries to answer all outstanding ad-hoc data requests",
            "Manually pull and merge data from all sources to build the quarterly board report",
            "Update the analytics tracking plan and manually verify all new event properties today",
            "Manually investigate and fix the broken data pipeline before the next scheduled refresh",
            "Manually compile executive dashboard data from all tools for the weekly leadership review",
            "Write manual data extraction script to pull this quarters customer revenue cohort",
            "Manually refresh all Tableau workbooks and validate figures before distributing today",
            "Run manual data quality and consistency checks across all production database tables",
            "Manually join all required datasets and build the ad-hoc report requested by product",
        ],
        "blockers": [
            {
                "reporter": "h.zimmermann@testcorp.com",
                "title": "No automated data pipeline — all reports generated manually each week",
                "description": "Analysts manually pull from Postgres, Stripe, Mixpanel, and Google Sheets every week. The pipeline breaks at least once weekly. 6+ hours of manual work per reporting cycle.",
                "status": "open",
                "days_ago": 7,
            },
            {
                "reporter": "p.singh@testcorp.com",
                "title": "Ad-hoc SQL requests consuming 40% of data team capacity weekly",
                "description": "Business teams request custom queries via Slack. No self-serve analytics tool. Data team writing and running manual SQL for 15-20 ad-hoc requests every single week.",
                "status": "in_progress",
                "days_ago": 12,
            },
            {
                "reporter": "s.kim@testcorp.com",
                "title": "Tableau workbooks break silently after every DB schema change",
                "description": "No automated schema change detection. When engineers alter table structures, 5-10 Tableau reports break silently. Discovered only when stakeholders report seeing stale data.",
                "status": "acknowledged",
                "days_ago": 18,
            },
            {
                "reporter": "n.blake@testcorp.com",
                "title": "No data quality monitoring in production — issues found after reports sent",
                "description": "No dbt tests or runtime data quality checks configured. Null IDs, negative revenue, and duplicate rows have been found only after reports were delivered to leadership.",
                "status": "open",
                "days_ago": 23,
            },
        ],
    },
]

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


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    async with _Session() as session:
        async with session.begin():

            # ── Guard ─────────────────────────────────────────────────────────
            guard_result = await session.execute(
                select(Team).where(Team.name == "[TEST] Founder's Office")
            )
            if guard_result.scalar_one_or_none():
                print(
                    "⚠️  Large test data already exists ('[TEST] Founder\\'s Office' found).\n"
                    "   Run `python3 remove_test_data.py` first to reseed."
                )
                return

            hashed_pw = hash_password(TEST_PASSWORD)
            today     = date.today()
            now       = datetime.utcnow()

            # ── Get or create manager ─────────────────────────────────────────
            mgr_result = await session.execute(
                select(User).where(User.email == MANAGER_EMAIL)
            )
            manager = mgr_result.scalar_one_or_none()
            if not manager:
                manager = User(
                    email=MANAGER_EMAIL,
                    name=MANAGER_NAME,
                    password=hashed_pw,
                    role="manager",
                    created_at=now - timedelta(days=40),
                )
                session.add(manager)
                await session.flush()
                print(f"  Created manager: {MANAGER_EMAIL}")
            else:
                print(f"  Using existing manager: {MANAGER_EMAIL}")

            total_checkins   = 0
            total_members    = 0
            total_blockers   = 0

            for team_cfg in TEAMS:

                # ── Create member user accounts ───────────────────────────────
                team_users: dict[str, User] = {}
                for name, email, rate, hours, confirmed, send_t in team_cfg["members"]:
                    u = User(
                        email=email,
                        name=name,
                        password=hashed_pw,
                        role="member",
                        created_at=now - timedelta(days=35),
                    )
                    session.add(u)
                    team_users[email] = u
                await session.flush()

                # ── Create team (Starter plan) ────────────────────────────────
                team = Team(
                    name=team_cfg["name"],
                    manager_id=manager.id,
                    plan="starter",
                    plan_status="active",
                    team_type=team_cfg["team_type"],
                    currency="USD",
                    created_at=now - timedelta(days=36),
                )
                session.add(team)
                await session.flush()

                # ── Create TeamMember records ─────────────────────────────────
                for name, email, rate, hours, confirmed, send_t in team_cfg["members"]:
                    tm = TeamMember(
                        team_id=team.id,
                        user_id=team_users[email].id,
                        status="active",
                        role="member",
                        hourly_rate=rate,
                        currency="USD",
                        hours_per_day=hours,
                        hours_confirmed=confirmed if rate is not None else False,
                        timezone="America/New_York",
                        send_time=send_t,
                        created_at=now - timedelta(days=34),
                    )
                    session.add(tm)
                await session.flush()

                # ── Create TeamQuestions ──────────────────────────────────────
                q_objects: list[TeamQuestion] = []
                for idx, (label, is_blocker) in enumerate(team_cfg["questions"]):
                    q = TeamQuestion(
                        team_id=team.id,
                        order_index=idx,
                        label=label,
                        enabled=True,
                        is_blocker_type=is_blocker,
                        created_at=now - timedelta(days=33),
                    )
                    session.add(q)
                    q_objects.append(q)
                await session.flush()

                q_yesterday = q_objects[0]
                q_today_q   = q_objects[1]
                q_blocker   = q_objects[2] if len(q_objects) > 2 else None

                y_phrases = team_cfg["yesterday_phrases"]
                t_phrases = team_cfg["today_phrases"]

                # ── Create 30 days of check-in history ───────────────────────
                team_checkin_count = 0
                for day_idx in range(SEED_DAYS):
                    target_date  = today - timedelta(days=SEED_DAYS - 1 - day_idx)
                    created_time = datetime.combine(target_date, datetime.min.time()).replace(
                        hour=1, minute=0, second=0
                    )

                    for m_idx, (name, email, *_rest) in enumerate(team_cfg["members"]):
                        if not _should_submit(
                            team_cfg["name"], email, day_idx, team_cfg["checkin_rate"]
                        ):
                            continue

                        submit_time = _submit_time(email, day_idx, target_date)

                        checkin = Checkin(
                            team_id=team.id,
                            user_id=team_users[email].id,
                            date=target_date,
                            checkin_token=str(uuid.uuid4()),
                            token_used=True,
                            submitted_at=submit_time,
                            created_at=created_time,
                        )
                        session.add(checkin)
                        await session.flush()

                        # Yesterday answer — cycle through phrases with per-member offset
                        y_idx = (day_idx + m_idx * 3) % len(y_phrases)
                        session.add(CheckinAnswer(
                            checkin_id=checkin.id,
                            question_id=q_yesterday.id,
                            answer=y_phrases[y_idx],
                            created_at=submit_time,
                        ))

                        # Today answer
                        t_idx = (day_idx + m_idx * 2) % len(t_phrases)
                        session.add(CheckinAnswer(
                            checkin_id=checkin.id,
                            question_id=q_today_q.id,
                            answer=t_phrases[t_idx],
                            created_at=submit_time,
                        ))

                        # Blocker question — empty (no blocker from check-in flow)
                        if q_blocker:
                            session.add(CheckinAnswer(
                                checkin_id=checkin.id,
                                question_id=q_blocker.id,
                                answer="",
                                created_at=submit_time,
                            ))

                        team_checkin_count += 1

                await session.flush()
                total_checkins += team_checkin_count
                total_members  += len(team_cfg["members"])

                # ── Create blockers ───────────────────────────────────────────
                for bd in team_cfg["blockers"]:
                    reporter_user = team_users[bd["reporter"]]
                    created_at    = now - timedelta(days=bd["days_ago"])
                    resolved_at   = (
                        now - timedelta(days=bd["resolved_days_ago"])
                        if "resolved_days_ago" in bd else None
                    )
                    b = Blocker(
                        team_id=team.id,
                        user_id=reporter_user.id,
                        status=bd["status"],
                        title=bd["title"],
                        description=bd["description"],
                        created_at=created_at,
                        updated_at=created_at,
                        resolved_at=resolved_at,
                    )
                    session.add(b)
                    total_blockers += 1

                await session.flush()

                member_count = len(team_cfg["members"])
                print(
                    f"  ✓ {team_cfg['name']:<35} "
                    f"{member_count:>2} members  |  "
                    f"~{int(team_cfg['checkin_rate']*100)}% rate  |  "
                    f"{team_checkin_count} check-ins  |  "
                    f"{len(team_cfg['blockers'])} blockers"
                )

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("✅  Large test data seeded successfully!")
    print("=" * 60)
    print(f"  Teams    : {len(TEAMS)}")
    print(f"  Members  : {total_members}  (@testcorp.com accounts)")
    print(f"  Check-ins: {total_checkins}  over {SEED_DAYS} days per team")
    print(f"  Blockers : {total_blockers}  total across all teams")
    print()
    print(f"  Login as manager : {MANAGER_EMAIL}  /  {TEST_PASSWORD}")
    print()
    print("  Dashboard will show all 10 teams in the team selector.")
    print("  Reports, Cost Intelligence, and Automation Radar all work")
    print("  on any team — select a team from the reports page dropdown.")
    print()
    print("  To remove all test data: python3 remove_test_data.py")
    print()

    await _engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
