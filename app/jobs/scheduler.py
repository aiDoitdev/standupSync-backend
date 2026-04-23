import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.jobs.checkin_job import send_daily_emails
from app.jobs.ai_radar_job import run_due_ai_task_radar
from app.jobs.billing_job import reconcile_subscriptions

logger = structlog.get_logger(__name__)
scheduler = AsyncIOScheduler()


def start_scheduler() -> None:
    scheduler.add_job(send_daily_emails,      CronTrigger(minute="*"),    id="daily_checkin_emails",    replace_existing=True)
    scheduler.add_job(run_due_ai_task_radar,  CronTrigger(minute="*/10"), id="ai_task_radar_poller",    replace_existing=True)
    scheduler.add_job(reconcile_subscriptions, CronTrigger(hour="*/6"),   id="subscription_reconciler", replace_existing=True)
    scheduler.start()
    logger.info("scheduler.started", jobs=["daily_checkin_emails(1min)", "ai_radar(10min)", "billing_reconcile(6h)"])
