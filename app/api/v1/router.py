from fastapi import APIRouter

from app.api.v1 import auth, teams, checkin, invite, waitlist, blockers, billing, reports, automation, ai_task_radar, dashboard

router = APIRouter(prefix="/v1")

router.include_router(auth.router,          prefix="/auth",         tags=["auth"])
router.include_router(teams.router,         prefix="/teams",        tags=["teams"])
router.include_router(checkin.router,       prefix="/checkin",      tags=["checkin"])
router.include_router(invite.router,        prefix="/invite",       tags=["invite"])
router.include_router(waitlist.router,      prefix="/waitlist",     tags=["waitlist"])
router.include_router(blockers.router,      prefix="/blockers",     tags=["blockers"])
router.include_router(billing.router,       prefix="/billing",      tags=["billing"])
router.include_router(reports.router,       prefix="/reports",      tags=["reports"])
router.include_router(automation.router,    prefix="/automation",   tags=["automation"])
router.include_router(ai_task_radar.router, prefix="/ai-task-radar", tags=["ai-task-radar"])
router.include_router(dashboard.router,     prefix="/dashboard",    tags=["dashboard"])
