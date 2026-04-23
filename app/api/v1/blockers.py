import structlog
from datetime import datetime, date, timezone
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from app.core.database import get_db
from app.models import (
    Blocker,
    BlockerComment,
    BlockerResolution,
    Team,
    User,
)
from app.schemas import (
    BlockerListItemResponse,
    BlockerDetailResponse,
    BlockerCommentResponse,
    AddBlockerCommentRequest,
    UpdateBlockerStatusRequest,
    ResolveBlockerRequest,
    AssignBlockerRequest,
)
from app.core.dependencies import (
    get_current_user,
    require_team_access,
    require_team_manager,
)
from app.services.email_service import (
    send_blocker_comment_email,
    send_blocker_resolution_email,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/{team_id}/list", response_model=list[BlockerListItemResponse])
async def list_blockers(
    team_id: str,
    status_filter: str = Query(None),
    user_id: str = Query(None),
    date_from: date = Query(None),
    date_to: date = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List blockers for a team with optional filters. User must be in team."""
    team, member = await require_team_access(team_id, current_user, db)

    query = select(Blocker).where(Blocker.team_id == team.id)

    # Members only see blockers they created OR are assigned to
    if member.role == "member":
        from sqlalchemy import or_
        query = query.where(
            or_(
                Blocker.user_id == current_user.id,
                Blocker.assigned_to == current_user.id,
            )
        )

    if status_filter:
        query = query.where(Blocker.status == status_filter)

    if user_id:
        query = query.where(Blocker.user_id == user_id)

    if date_from:
        query = query.where(Blocker.created_at >= datetime.combine(date_from, datetime.min.time()))

    if date_to:
        query = query.where(Blocker.created_at <= datetime.combine(date_to, datetime.max.time()))

    query = query.order_by(Blocker.created_at.desc())

    result = await db.execute(query)
    blockers = result.scalars().all()

    response = []
    for blocker in blockers:
        # Get comment count
        comment_result = await db.execute(
            select(func.count(BlockerComment.id)).where(
                BlockerComment.blocker_id == blocker.id
            )
        )
        comment_count = comment_result.scalar() or 0

        # Get reporter name
        user_result = await db.execute(select(User).where(User.id == blocker.user_id))
        user = user_result.scalar_one_or_none()

        # Get assigned member name
        assigned_to_name = None
        if blocker.assigned_to:
            assigned_result = await db.execute(select(User).where(User.id == blocker.assigned_to))
            assigned_user = assigned_result.scalar_one_or_none()
            assigned_to_name = assigned_user.name if assigned_user else None

        response.append(
            BlockerListItemResponse(
                id=str(blocker.id),
                team_id=str(blocker.team_id),
                user_id=str(blocker.user_id),
                user_name=user.name if user else None,
                assigned_to=str(blocker.assigned_to) if blocker.assigned_to else None,
                assigned_to_name=assigned_to_name,
                status=blocker.status,
                title=blocker.title,
                created_at=blocker.created_at,
                updated_at=blocker.updated_at,
                comment_count=comment_count,
                resolved_at=blocker.resolved_at,
            )
        )

    return response


@router.get("/{team_id}/{blocker_id}", response_model=BlockerDetailResponse)
async def get_blocker_detail(
    team_id: str,
    blocker_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get blocker details with comments and resolution."""
    team, _ = await require_team_access(team_id, current_user, db)

    # Get blocker
    result = await db.execute(
        select(Blocker).where(
            and_(Blocker.id == blocker_id, Blocker.team_id == team.id)
        )
    )
    blocker = result.scalar_one_or_none()

    if not blocker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Blocker not found",
        )

    # Get member info
    user_result = await db.execute(select(User).where(User.id == blocker.user_id))
    user = user_result.scalar_one_or_none()

    # Get assigned member info
    assigned_to_name = None
    if blocker.assigned_to:
        assigned_result = await db.execute(select(User).where(User.id == blocker.assigned_to))
        assigned_user = assigned_result.scalar_one_or_none()
        assigned_to_name = assigned_user.name if assigned_user else None

    # Get comments
    comments_result = await db.execute(
        select(BlockerComment, User)
        .join(User, BlockerComment.user_id == User.id)
        .where(BlockerComment.blocker_id == blocker.id)
        .order_by(BlockerComment.created_at)
    )
    comments_rows = comments_result.all()

    comments = []
    for comment, comment_user in comments_rows:
        comments.append(
            BlockerCommentResponse(
                id=str(comment.id),
                user_id=str(comment.user_id),
                user_name=comment_user.name,
                comment=comment.comment,
                created_at=comment.created_at,
            )
        )

    # Get resolution if exists
    resolution_result = await db.execute(
        select(BlockerResolution, User)
        .join(User, BlockerResolution.manager_id == User.id)
        .where(BlockerResolution.blocker_id == blocker.id)
    )
    resolution_row = resolution_result.first()

    resolution = None
    if resolution_row:
        res, manager = resolution_row
        resolution = get_blocker_resolution_response(res, manager)

    return BlockerDetailResponse(
        id=str(blocker.id),
        team_id=str(blocker.team_id),
        user_id=str(blocker.user_id),
        user_name=user.name if user else None,
        user_email=user.email if user else None,
        assigned_to=str(blocker.assigned_to) if blocker.assigned_to else None,
        assigned_to_name=assigned_to_name,
        status=blocker.status,
        title=blocker.title,
        description=blocker.description,
        created_at=blocker.created_at,
        updated_at=blocker.updated_at,
        resolved_at=blocker.resolved_at,
        comments=comments,
        resolution=resolution,
    )


@router.patch("/{team_id}/{blocker_id}/status", status_code=status.HTTP_200_OK)
async def update_blocker_status(
    team_id: str,
    blocker_id: str,
    data: UpdateBlockerStatusRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update blocker status. Manager only."""
    team, _ = await require_team_manager(team_id, current_user, db)

    # Get blocker
    result = await db.execute(
        select(Blocker).where(
            and_(Blocker.id == blocker_id, Blocker.team_id == team.id)
        )
    )
    blocker = result.scalar_one_or_none()

    if not blocker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Blocker not found",
        )

    # Valid status transitions
    # "resolved" is intentionally excluded here — use POST /{blocker_id}/resolve instead
    # to ensure unblock instructions and member email are always included.
    valid_statuses = ["open", "acknowledged", "in_progress"]
    if data.status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Must be one of: {valid_statuses}. To resolve, use the /resolve endpoint.",
        )

    blocker.status = data.status
    blocker.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.add(blocker)
    await db.commit()

    return {"message": f"Blocker status updated to {data.status}"}


@router.patch("/{team_id}/{blocker_id}/assign", status_code=status.HTTP_200_OK)
async def assign_blocker(
    team_id: str,
    blocker_id: str,
    data: AssignBlockerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Assign (or unassign) a blocker. Manager or the currently-assigned member."""
    team, _ = await require_team_access(team_id, current_user, db)

    result = await db.execute(
        select(Blocker).where(
            and_(Blocker.id == blocker_id, Blocker.team_id == team.id)
        )
    )
    blocker = result.scalar_one_or_none()

    if not blocker:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blocker not found")

    # Only the manager OR the currently-assigned member may reassign
    is_manager = team.manager_id == current_user.id
    is_assigned = blocker.assigned_to and str(blocker.assigned_to) == str(current_user.id)
    if not is_manager and not is_assigned:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the manager or assigned member can reassign this blocker")

    import uuid as _uuid
    if data.assigned_to:
        try:
            assigned_uuid = _uuid.UUID(data.assigned_to)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user id")

        # Verify the assignee is an active team member
        from app.models.team import TeamMember
        member_result = await db.execute(
            select(TeamMember).where(
                and_(
                    TeamMember.team_id == team.id,
                    TeamMember.user_id == assigned_uuid,
                    TeamMember.status == "active",
                )
            )
        )
        if not member_result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is not an active team member")

        blocker.assigned_to = assigned_uuid
    else:
        blocker.assigned_to = None

    blocker.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(blocker)
    await db.commit()

    return {"message": "Blocker assignment updated"}


@router.post("/{team_id}/{blocker_id}/comment", status_code=status.HTTP_201_CREATED)
async def add_blocker_comment(
    team_id: str,
    blocker_id: str,
    data: AddBlockerCommentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a comment to a blocker. Triggers email notification."""
    team, _ = await require_team_access(team_id, current_user, db)

    # Get blocker
    result = await db.execute(
        select(Blocker).where(
            and_(Blocker.id == blocker_id, Blocker.team_id == team.id)
        )
    )
    blocker = result.scalar_one_or_none()

    if not blocker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Blocker not found",
        )

    # Add comment
    comment = BlockerComment(
        blocker_id=blocker.id,
        user_id=current_user.id,
        comment=data.comment,
    )
    db.add(comment)
    blocker.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(blocker)
    await db.commit()
    await db.refresh(comment)

    # Get other party for notification
    member_result = await db.execute(
        select(User).where(User.id == blocker.user_id)
    )
    member = member_result.scalar_one_or_none()

    # Send email notification
    try:
        if current_user.id == blocker.user_id:
            # Member replied - notify manager
            result = await db.execute(select(Team).where(Team.id == team.id))
            team_obj = result.scalar_one_or_none()

            manager_result = await db.execute(
                select(User).where(User.id == team_obj.manager_id)
            )
            manager = manager_result.scalar_one_or_none()

            send_blocker_comment_email(
                manager_email=manager.email,
                manager_name=manager.name,
                member_name=current_user.name,
                blocker_title=blocker.title,
                comment=data.comment,
                team_name=team_obj.name,
            )
        else:
            # Manager commented - notify member
            result = await db.execute(select(Team).where(Team.id == team.id))
            team_obj = result.scalar_one_or_none()

            send_blocker_comment_email(
                member_email=member.email,
                member_name=member.name,
                manager_name=current_user.name,
                blocker_title=blocker.title,
                comment=data.comment,
                team_name=team_obj.name,
                team_id=str(team_obj.id),
            )
    except Exception as e:
        logger.error("Failed to send blocker comment email: %s", e)

    return {
        "id": str(comment.id),
        "message": "Comment added successfully",
    }


@router.post("/{team_id}/{blocker_id}/resolve", status_code=status.HTTP_200_OK)
async def resolve_blocker(
    team_id: str,
    blocker_id: str,
    data: ResolveBlockerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resolve a blocker with instructions. Manager or the assigned member."""
    team, _ = await require_team_access(team_id, current_user, db)

    # Get blocker
    result = await db.execute(
        select(Blocker).where(
            and_(Blocker.id == blocker_id, Blocker.team_id == team.id)
        )
    )
    blocker = result.scalar_one_or_none()

    if not blocker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Blocker not found",
        )

    # Only the manager OR the currently-assigned member may resolve
    is_manager = team.manager_id == current_user.id
    is_assigned = blocker.assigned_to and str(blocker.assigned_to) == str(current_user.id)
    if not is_manager and not is_assigned:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the manager or assigned member can resolve this blocker")

    # Create resolution record
    resolution = BlockerResolution(
        blocker_id=blocker.id,
        manager_id=current_user.id,
        unblock_instructions=data.unblock_instructions,
    )
    db.add(resolution)

    # Update blocker
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    blocker.status = "resolved"
    blocker.resolved_at = now_utc
    blocker.updated_at = now_utc
    db.add(blocker)

    await db.commit()

    # Get member and team info for email
    member_result = await db.execute(select(User).where(User.id == blocker.user_id))
    member = member_result.scalar_one_or_none()

    team_result = await db.execute(select(Team).where(Team.id == team.id))
    team_obj = team_result.scalar_one_or_none()

    # Send email notification
    try:
        send_blocker_resolution_email(
            member_email=member.email,
            member_name=member.name,
            manager_name=current_user.name,
            blocker_title=blocker.title,
            unblock_instructions=data.unblock_instructions,
            team_name=team_obj.name,
        )
    except Exception as e:
        logger.error("Failed to send blocker resolution email: %s", e)

    return {
        "message": "Blocker resolved and member notified",
    }


def get_blocker_resolution_response(resolution, manager):
    from app.schemas.blocker import BlockerResolutionResponse
    return BlockerResolutionResponse(
        id=str(resolution.id),
        manager_id=str(resolution.manager_id),
        manager_name=manager.name if manager else None,
        unblock_instructions=resolution.unblock_instructions,
        created_at=resolution.created_at,
    )
