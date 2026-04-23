import os
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import resend
from dotenv import load_dotenv

load_dotenv()

resend.api_key = os.getenv("RESEND_API_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://aidoit.dev")

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}" + {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _week_of_month(day: int) -> int:
    return (day - 1) // 7 + 1


def _score_color(score: int) -> str:
    if score >= 80:
        return "#10b981"
    if score >= 60:
        return "#f59e0b"
    return "#ef4444"


def _tier_color(tier: str) -> str:
    return {"P1": "#4F46E5", "P2": "#0891b2"}.get(tier, "#6b7280")


def _local_dt_str(dt_naive_utc, tz_str: str) -> str:
    """Format a naive-UTC datetime into a human-readable local time string."""
    if dt_naive_utc is None:
        return "—"
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, Exception):
        tz = ZoneInfo("UTC")
    dt_local = dt_naive_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    return dt_local.strftime("%a, %d %b %Y at %I:%M %p")


def send_invite_email(to_email: str, team_name: str, invite_token: str) -> None:
    """Send a team invite email with a join link."""
    invite_link = f"{FRONTEND_URL}/join/{invite_token}"
    resend.Emails.send({
        "from": "StandupSync <noreply@aidoit.dev>",
        "to": [to_email],
        "subject": f"You've been invited to join {team_name} on StandupSync",
        "html": f"""
<!DOCTYPE html>
<html>
<body style="font-family: Inter, Arial, sans-serif; background: #f9fafb; padding: 40px 0;">
  <div style="max-width: 480px; margin: 0 auto; background: #ffffff;
              border-radius: 12px; border: 1px solid #e5e7eb; padding: 40px;">
    <h1 style="font-size: 24px; font-weight: 700; color: #111827; margin: 0 0 8px;">
      You&rsquo;ve been invited! 🎉
    </h1>
    <p style="color: #6b7280; margin: 0 0 24px;">
      You&rsquo;ve been added to <strong>{team_name}</strong> on StandupSync &mdash;
      a daily check-in tool for remote teams.
    </p>
    <p style="color: #374151; margin: 0 0 24px;">
      Every morning you&rsquo;ll get a short email with 3 questions. It takes
      about 30 seconds to answer.
    </p>
    <a href="{invite_link}"
       style="display: inline-block; background: #4F46E5; color: #ffffff;
              text-decoration: none; padding: 12px 28px; border-radius: 8px;
              font-weight: 600; font-size: 16px;">
      Join Team →
    </a>
    <p style="color: #9ca3af; font-size: 13px; margin: 28px 0 0;">
      This link expires in 7 days. If you didn&rsquo;t expect this email, you can safely ignore it.
    </p>
  </div>
</body>
</html>
""",
    })


def send_daily_checkin_email(
    to_email: str,
    member_name: str,
    team_name: str,
    checkin_token: str,
    date_str: str,
) -> None:
    """Send the daily morning check-in email with a magic link."""
    checkin_link = f"{FRONTEND_URL}/checkin/{checkin_token}"
    resend.Emails.send({
        "from": "StandupSync <noreply@aidoit.dev>",
        "to": [to_email],
        "subject": f"⏰ Daily Check-in — {team_name} — {date_str}",
        "html": f"""
<!DOCTYPE html>
<html>
<body style="font-family: Inter, Arial, sans-serif; background: #f9fafb; padding: 40px 0;">
  <div style="max-width: 480px; margin: 0 auto; background: #ffffff;
              border-radius: 12px; border: 1px solid #e5e7eb; padding: 40px;">
    <h1 style="font-size: 24px; font-weight: 700; color: #111827; margin: 0 0 8px;">
      Good morning, {member_name}! ☀️
    </h1>
    <p style="color: #6b7280; margin: 0 0 4px;">
      <strong>{team_name}</strong> Daily Check-in &mdash; {date_str}
    </p>
    <p style="color: #374151; margin: 16px 0 24px;">
      It&rsquo;s time for your daily standup. Click below &mdash; it takes 30 seconds!
    </p>
    <a href="{checkin_link}"
       style="display: inline-block; background: #4F46E5; color: #ffffff;
              text-decoration: none; padding: 14px 32px; border-radius: 8px;
              font-weight: 600; font-size: 16px;">
      Submit Today&rsquo;s Update →
    </a>
    <div style="margin-top: 28px; padding: 16px; background: #f9fafb;
                border-radius: 8px; border: 1px solid #e5e7eb;">
      <p style="color: #374151; font-size: 14px; margin: 0 0 8px; font-weight: 600;">
        Three quick questions:
      </p>
      <p style="color: #6b7280; font-size: 14px; margin: 0 0 4px;">
        ✅ What did you accomplish yesterday?
      </p>
      <p style="color: #6b7280; font-size: 14px; margin: 0 0 4px;">
        🎯 What will you work on today?
      </p>
      <p style="color: #6b7280; font-size: 14px; margin: 0;">
        🚧 Any blockers or issues?
      </p>
    </div>
    <p style="color: #9ca3af; font-size: 13px; margin: 24px 0 0;">
      This link expires in 24 hours. Only you can use it.
    </p>
  </div>
</body>
</html>
""",
    })


def send_blocker_comment_email(
    member_email: str = None,
    manager_email: str = None,
    member_name: str = None,
    manager_name: str = None,
    blocker_title: str = None,
    comment: str = None,
    team_name: str = None,
    team_id: str = None,
) -> None:
    """Send email when manager/member comments on a blocker."""
    is_member_notification = bool(member_email)
    recipient_email = member_email or manager_email
    recipient_name = member_name or manager_name
    commenter_name = manager_name if is_member_notification else member_name
    action_text = "asked a question about" if is_member_notification else "replied to"
    blocker_link = f"{FRONTEND_URL}/blockers?team_id={team_id}" if team_id else f"{FRONTEND_URL}/blockers"

    resend.Emails.send({
        "from": "StandupSync <noreply@aidoit.dev>",
        "to": [recipient_email],
        "subject": f"💬 Update on blocker: {blocker_title}",
        "html": f"""
<!DOCTYPE html>
<html>
<body style="font-family: Inter, Arial, sans-serif; background: #f9fafb; padding: 40px 0;">
  <div style="max-width: 480px; margin: 0 auto; background: #ffffff;
              border-radius: 12px; border: 1px solid #e5e7eb; padding: 40px;">
    <h1 style="font-size: 24px; font-weight: 700; color: #111827; margin: 0 0 8px;">
      {commenter_name} {action_text} your blocker 💬
    </h1>
    <p style="color: #6b7280; margin: 0 0 12px;">
      <strong>{team_name}</strong>
    </p>
    
    <div style="margin: 24px 0; padding: 16px; background: #f3f4f6;
                border-left: 4px solid #fbbf24; border-radius: 4px;">
      <p style="color: #374151; font-weight: 600; margin: 0 0 8px;">
        Blocker: {blocker_title}
      </p>
      <p style="color: #6b7280; margin: 0;">
        {comment}
      </p>
    </div>

    <p style="color: #374151; margin: 24px 0;">
      Log in to StandupSync to see the full discussion and reply.
    </p>

    <a href="{blocker_link}"
       style="display: inline-block; background: #4F46E5; color: #ffffff;
              text-decoration: none; padding: 12px 28px; border-radius: 8px;
              font-weight: 600; font-size: 16px;">
      View Blocker &amp; Reply →
    </a>

    <p style="color: #9ca3af; font-size: 13px; margin: 28px 0 0;">
      This is an automated notification from StandupSync.
    </p>
  </div>
</body>
</html>
""",
    })


def send_blocker_resolution_email(
    member_email: str,
    member_name: str,
    manager_name: str,
    blocker_title: str,
    unblock_instructions: str,
    team_name: str,
) -> None:
    """Send email when manager unblocks a blocker with instructions."""
    resend.Emails.send({
        "from": "StandupSync <noreply@aidoit.dev>",
        "to": [member_email],
        "subject": f"✅ Your blocker has been unblocked: {blocker_title}",
        "html": f"""
<!DOCTYPE html>
<html>
<body style="font-family: Inter, Arial, sans-serif; background: #f9fafb; padding: 40px 0;">
  <div style="max-width: 480px; margin: 0 auto; background: #ffffff;
              border-radius: 12px; border: 1px solid #e5e7eb; padding: 40px;">
    <h1 style="font-size: 24px; font-weight: 700; color: #10b981; margin: 0 0 8px;">
      ✅ Your blocker has been unblocked!
    </h1>
    <p style="color: #6b7280; margin: 0 0 24px;">
      <strong>{manager_name}</strong> has provided a solution for your blocker in <strong>{team_name}</strong>.
    </p>
    
    <div style="margin: 24px 0; padding: 16px; background: #ecfdf5;
                border-left: 4px solid #10b981; border-radius: 4px;">
      <p style="color: #374151; font-weight: 600; margin: 0 0 8px;">
        {blocker_title}
      </p>
      <p style="color: #059669; margin: 0; white-space: pre-wrap; word-wrap: break-word;">
        {unblock_instructions}
      </p>
    </div>

    <p style="color: #374151; margin: 24px 0;">
      Follow the instructions above to unblock yourself. If you need further clarification,
      you can reply directly in the StandupSync dashboard.
    </p>

    <a href="{FRONTEND_URL}/dashboard"
       style="display: inline-block; background: #10b981; color: #ffffff;
              text-decoration: none; padding: 12px 28px; border-radius: 8px;
              font-weight: 600; font-size: 16px;">
      View Full Details →
    </a>

    <p style="color: #9ca3af; font-size: 13px; margin: 28px 0 0;">
      This is an automated notification from StandupSync.
    </p>
  </div>
</body>
</html>
""",
    })


def send_ai_task_radar_empty_email(
    manager_email: str,
    manager_name: str,
    team_name: str,
    team_id: str,
    window_days: int,
) -> None:
    """Nudge the manager when a scheduled Ai Task Radar run had zero check-in data."""
    radar_link = f"{FRONTEND_URL}/reports/ai?team_id={team_id}"
    resend.Emails.send({
        "from": "StandupSync <noreply@aidoit.dev>",
        "to": [manager_email],
        "subject": f"🤖 Ai Task Radar — no data this week for {team_name}",
        "html": f"""
<!DOCTYPE html>
<html>
<body style="font-family: Inter, Arial, sans-serif; background: #f9fafb; padding: 40px 0;">
  <div style="max-width: 480px; margin: 0 auto; background: #ffffff;
              border-radius: 12px; border: 1px solid #e5e7eb; padding: 40px;">
    <h1 style="font-size: 22px; font-weight: 700; color: #111827; margin: 0 0 8px;">
      Hi {manager_name} — Ai Task Radar ran, but there&rsquo;s nothing to analyse yet
    </h1>
    <p style="color: #6b7280; margin: 0 0 16px;">
      Your scheduled Ai Task Radar run just fired for <strong>{team_name}</strong>, but
      no one submitted check-ins in the last {window_days} days.
    </p>
    <p style="color: #374151; margin: 0 0 24px;">
      Nudge your team to fill in their daily stand-up so we can surface real automation
      opportunities on the next run.
    </p>
    <a href="{radar_link}"
       style="display: inline-block; background: #4F46E5; color: #ffffff;
              text-decoration: none; padding: 12px 28px; border-radius: 8px;
              font-weight: 600; font-size: 16px;">
      Open Ai Task Radar →
    </a>
    <p style="color: #9ca3af; font-size: 13px; margin: 28px 0 0;">
      You can adjust the cadence, day or time from the same page.
    </p>
  </div>
</body>
</html>
""",
    })


def send_schedule_config_ack_email(
    manager_email: str,
    manager_name: str,
    team_name: str,
    team_id: str,
    cadence: str,
    day_of_week: int,
    week_of_month,
    run_time: str,
    timezone_str: str,
    enabled: bool,
    next_run_at_utc,
) -> None:
    """Acknowledge a schedule configuration save to the team owner."""
    schedule_link = f"{FRONTEND_URL}/reports/ai?team_id={team_id}"

    cadence_labels = {"weekly": "Every week", "biweekly": "Every 2 weeks", "monthly": "Monthly"}
    cadence_label = cadence_labels.get(cadence, cadence.capitalize())
    if cadence == "monthly" and week_of_month:
        cadence_label = f"Monthly (Week {week_of_month})"

    day_label = _DAY_NAMES[day_of_week] if 0 <= day_of_week <= 6 else "—"

    try:
        hh, mm = run_time.split(":")
        h, m = int(hh), int(mm)
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        time_label = f"{h12:02d}:{mm} {ampm}"
    except Exception:
        time_label = run_time

    status_badge = (
        '<span style="background:#d1fae5;color:#065f46;padding:2px 10px;'
        'border-radius:20px;font-size:13px;font-weight:600;">&#x2705; Active</span>'
        if enabled else
        '<span style="background:#f3f4f6;color:#6b7280;padding:2px 10px;'
        'border-radius:20px;font-size:13px;font-weight:600;">&#x23F8; Paused</span>'
    )

    next_run_label = _local_dt_str(next_run_at_utc, timezone_str) if enabled else "—"

    resend.Emails.send({
        "from": "StandupSync <noreply@aidoit.dev>",
        "to": [manager_email],
        "subject": f"✅ Schedule Saved — {team_name} · Ai Task Radar",
        "html": f"""
<!DOCTYPE html>
<html>
<body style="font-family: Inter, Arial, sans-serif; background: #f5f3ff; padding: 40px 0;">
  <div style="max-width: 520px; margin: 0 auto;">

    <!-- Header -->
    <div style="background: linear-gradient(135deg, #4F46E5 0%, #7c3aed 100%);
                border-radius: 12px 12px 0 0; padding: 28px 36px;">
      <p style="color: #c4b5fd; font-size: 13px; font-weight: 600; margin: 0 0 4px;
                letter-spacing: 0.08em; text-transform: uppercase;">Ai Task Radar</p>
      <h1 style="color: #ffffff; font-size: 22px; font-weight: 700; margin: 0;">
        Schedule Updated
      </h1>
    </div>

    <!-- Body -->
    <div style="background: #ffffff; border-radius: 0 0 12px 12px;
                border: 1px solid #e5e7eb; border-top: none; padding: 32px 36px;">
      <p style="color: #374151; margin: 0 0 20px;">
        Hi <strong>{manager_name}</strong>,
      </p>
      <p style="color: #6b7280; margin: 0 0 28px;">
        Your Ai Task Radar schedule for <strong>{team_name}</strong> has been saved.
        Here&rsquo;s a summary of the new configuration:
      </p>

      <!-- Schedule summary table -->
      <div style="background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 10px;
                  padding: 20px 24px; margin-bottom: 28px;">
        <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
          <tr>
            <td style="color: #9ca3af; padding: 7px 0; width: 42%; font-weight: 500;">Cadence</td>
            <td style="color: #111827; font-weight: 600;">{cadence_label}</td>
          </tr>
          <tr>
            <td style="color: #9ca3af; padding: 7px 0; font-weight: 500;">Day</td>
            <td style="color: #111827; font-weight: 600;">{day_label}</td>
          </tr>
          <tr>
            <td style="color: #9ca3af; padding: 7px 0; font-weight: 500;">Time</td>
            <td style="color: #111827; font-weight: 600;">{time_label}</td>
          </tr>
          <tr>
            <td style="color: #9ca3af; padding: 7px 0; font-weight: 500;">Timezone</td>
            <td style="color: #111827; font-weight: 600;">{timezone_str}</td>
          </tr>
          <tr>
            <td style="color: #9ca3af; padding: 7px 0; font-weight: 500;">Status</td>
            <td style="padding: 7px 0;">{status_badge}</td>
          </tr>
          <tr>
            <td style="color: #9ca3af; padding: 7px 0; font-weight: 500;">Next Run</td>
            <td style="color: #111827; font-weight: 600;">{next_run_label}</td>
          </tr>
        </table>
      </div>

      <a href="{schedule_link}"
         style="display: inline-block; background: #4F46E5; color: #ffffff;
                text-decoration: none; padding: 12px 28px; border-radius: 8px;
                font-weight: 600; font-size: 15px;">
        View Schedule &amp; Reports &rarr;
      </a>

      <p style="color: #9ca3af; font-size: 13px; margin: 28px 0 0;">
        You can modify the schedule anytime from the Ai Task Radar page.
        This is an automated notification from StandupSync.
      </p>
    </div>
  </div>
</body>
</html>
""",
    })


def send_team_report_email(
    manager_email: str,
    manager_name: str,
    team_name: str,
    team_id: str,
    analysis_id: str,
    team_score: int,
    period_start,
    period_end,
    top_tasks: list,
    summary_text: str,
    member_count: int,
    task_count: int,
) -> None:
    """Send the Ai Task Radar report to the team owner after a successful analysis run."""
    report_link = f"{FRONTEND_URL}/reports/ai?team_id={team_id}&analysis_id={analysis_id}"

    week_num = _week_of_month(period_end.day)
    start_str = f"{_MONTH_NAMES[period_start.month - 1]} {_ordinal(period_start.day)}"
    end_str = f"{_MONTH_NAMES[period_end.month - 1]} {_ordinal(period_end.day)}"
    period_label = f"Week {week_num}: {start_str} &ndash; {end_str}"

    score_color = _score_color(team_score)
    score_label = "Excellent" if team_score >= 80 else ("Good" if team_score >= 60 else "Needs Attention")

    # Top-3 tasks HTML
    tasks_html = ""
    for i, task in enumerate(top_tasks[:3], 1):
        tier = task.get("tier", "P3")
        tc = _tier_color(tier)
        sc = _score_color(task.get("score", 0))
        tasks_html += f"""
        <tr>
          <td style="padding: 12px 0; border-bottom: 1px solid #f3f4f6; vertical-align: top;">
            <div style="display: flex; align-items: flex-start; gap: 10px;">
              <span style="display: inline-block; background: {tc}1a; color: {tc};
                           font-size: 11px; font-weight: 700; padding: 2px 8px;
                           border-radius: 4px; white-space: nowrap; margin-top: 2px;">{tier}</span>
              <div>
                <p style="color: #111827; font-size: 14px; font-weight: 600; margin: 0 0 2px;">
                  {i}. {task.get('title', 'Untitled')}
                </p>
                <p style="color: #6b7280; font-size: 12px; margin: 0;">
                  {task.get('assigned_name', '')}
                </p>
              </div>
            </div>
          </td>
          <td style="padding: 12px 0; border-bottom: 1px solid #f3f4f6; text-align: right;
                     vertical-align: top; white-space: nowrap;">
            <span style="color: {sc}; font-size: 15px; font-weight: 700;">{task.get('score', 0)}</span>
            <span style="color: #9ca3af; font-size: 12px;">/100</span>
          </td>
        </tr>"""

    summary_snippet = (summary_text or "").strip()
    if len(summary_snippet) > 200:
        summary_snippet = summary_snippet[:200].rstrip() + "…"

    resend.Emails.send({
        "from": "StandupSync <noreply@aidoit.dev>",
        "to": [manager_email],
        "subject": f"📊 {team_name} — AI Report | Week {week_num} · Score {team_score}/100",
        "html": f"""
<!DOCTYPE html>
<html>
<body style="font-family: Inter, Arial, sans-serif; background: #f5f3ff; padding: 40px 0;">
  <div style="max-width: 560px; margin: 0 auto;">

    <!-- Header -->
    <div style="background: linear-gradient(135deg, #4F46E5 0%, #7c3aed 100%);
                border-radius: 12px 12px 0 0; padding: 28px 36px;">
      <p style="color: #c4b5fd; font-size: 13px; font-weight: 600; margin: 0 0 4px;
                letter-spacing: 0.08em; text-transform: uppercase;">Ai Task Radar Report</p>
      <h1 style="color: #ffffff; font-size: 22px; font-weight: 700; margin: 0 0 6px;">
        {team_name}
      </h1>
      <p style="color: #ddd6fe; font-size: 14px; margin: 0;">{period_label}</p>
    </div>

    <!-- Body -->
    <div style="background: #ffffff; border-radius: 0 0 12px 12px;
                border: 1px solid #e5e7eb; border-top: none; padding: 32px 36px;">

      <p style="color: #374151; margin: 0 0 24px;">
        Hi <strong>{manager_name}</strong>, your scheduled Ai Task Radar analysis for
        <strong>{team_name}</strong> is ready.
      </p>

      <!-- Score card -->
      <div style="background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 12px;
                  padding: 24px; text-align: center; margin-bottom: 28px;">
        <p style="color: #6b7280; font-size: 13px; font-weight: 600; margin: 0 0 8px;
                  text-transform: uppercase; letter-spacing: 0.06em;">Team AI Score</p>
        <div style="display: inline-block;">
          <span style="font-size: 56px; font-weight: 800; color: {score_color};
                       line-height: 1;">{team_score}</span>
          <span style="font-size: 24px; color: #9ca3af; font-weight: 400;">/100</span>
        </div>
        <p style="color: {score_color}; font-size: 14px; font-weight: 600; margin: 6px 0 0;">
          {score_label}
        </p>
      </div>

      {"" if not summary_snippet else f'''
      <!-- Summary -->
      <div style="border-left: 3px solid #c4b5fd; padding: 10px 16px;
                  background: #faf5ff; border-radius: 0 8px 8px 0; margin-bottom: 28px;">
        <p style="color: #5b21b6; font-size: 13px; margin: 0; line-height: 1.6;">
          {summary_snippet}
        </p>
      </div>
      '''}

      <!-- Top 3 tasks -->
      <h2 style="font-size: 15px; font-weight: 700; color: #111827; margin: 0 0 4px;">
        Top Automation Opportunities
      </h2>
      <p style="color: #9ca3af; font-size: 13px; margin: 0 0 16px;">
        Highest AI-scored tasks from this report
      </p>
      <table style="width: 100%; border-collapse: collapse;">
        {tasks_html}
      </table>

      <!-- Stats row -->
      <div style="display: flex; gap: 0; margin: 28px 0; background: #f9fafb;
                  border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden;">
        <div style="flex: 1; padding: 16px; text-align: center;
                    border-right: 1px solid #e5e7eb;">
          <p style="font-size: 24px; font-weight: 800; color: #4F46E5; margin: 0 0 2px;">
            {member_count}
          </p>
          <p style="font-size: 12px; color: #9ca3af; margin: 0; font-weight: 500;">
            Members Analysed
          </p>
        </div>
        <div style="flex: 1; padding: 16px; text-align: center;">
          <p style="font-size: 24px; font-weight: 800; color: #4F46E5; margin: 0 0 2px;">
            {task_count}
          </p>
          <p style="font-size: 12px; color: #9ca3af; margin: 0; font-weight: 500;">
            Tasks Identified
          </p>
        </div>
      </div>

      <a href="{report_link}"
         style="display: block; background: #4F46E5; color: #ffffff; text-align: center;
                text-decoration: none; padding: 14px 28px; border-radius: 8px;
                font-weight: 600; font-size: 15px; margin-bottom: 28px;">
        View Full Report &amp; Person-Specific Details &rarr;
      </a>

      <p style="color: #9ca3af; font-size: 13px; margin: 0; line-height: 1.6;">
        This report was automatically generated by Ai Task Radar based on your team&rsquo;s
        stand-up check-ins. Adjust your schedule anytime from the reports page.
      </p>
    </div>

    <!-- Footer -->
    <p style="text-align: center; color: #9ca3af; font-size: 12px; margin: 16px 0 0;">
      StandupSync &middot; Ai Task Radar &middot; Automated Report
    </p>
  </div>
</body>
</html>
""",
    })


def send_otp_email(to_email: str, otp_code: str) -> None:
    """Send a 6-digit OTP code to verify the user's email during signup."""
    resend.Emails.send({
        "from": "StandupSync <noreply@aidoit.dev>",
        "to": [to_email],
        "subject": f"{otp_code} — Your StandupSync verification code",
        "html": f"""
<!DOCTYPE html>
<html>
<body style="font-family: Inter, Arial, sans-serif; background: #f9fafb; padding: 40px 0;">
  <div style="max-width: 480px; margin: 0 auto; background: #ffffff;
              border-radius: 12px; border: 1px solid #e5e7eb; padding: 40px;">
    <h1 style="font-size: 24px; font-weight: 700; color: #111827; margin: 0 0 8px;">
      Verify your email &#x2709;&#xFE0F;
    </h1>
    <p style="color: #6b7280; margin: 0 0 24px;">
      Use the code below to complete your StandupSync account creation.
      This code expires in <strong>10 minutes</strong>.
    </p>
    <div style="text-align: center; margin: 32px 0;">
      <span style="display: inline-block; font-size: 40px; font-weight: 800;
                   letter-spacing: 12px; color: #4F46E5; background: #f5f3ff;
                   border-radius: 12px; padding: 16px 32px; border: 2px dashed #c4b5fd;">
        {otp_code}
      </span>
    </div>
    <p style="color: #6b7280; font-size: 14px; text-align: center; margin: 0;">
      If you didn&rsquo;t request this, you can safely ignore this email.
    </p>
  </div>
</body>
</html>
""",
    })
