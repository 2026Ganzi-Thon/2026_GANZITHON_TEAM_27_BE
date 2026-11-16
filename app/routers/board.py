"""비슷한 사건 게시판 (전시용: 체험 ID 기준)."""

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import serializers as ser
from app.config import get_settings
from app.db import get_db
from app.deps import demo_id_dep, limiter
from app.errors import ApiError, error_response, invalid_state, not_found
from app.models import CasePost, PostComment, PostLike, PostReport, utcnow
from app.routers.cases import load_case
from app.safety import needs_support, support_payload
from app.schemas import CommentIn, PostIn, ReportIn, UnlockIn
from app.utils import clamp_limit, hash_password, paginate, parse_uuid, verify_password

settings = get_settings()
router = APIRouter(tags=["board"])

NOT_FOUND_MSG = "글을 찾을 수 없거나 비밀번호가 틀렸어요."


def _new_post(demo_id: uuid.UUID, body: PostIn, case_id=None, pattern_tags=None) -> CasePost:
    published = settings.board_auto_publish
    return CasePost(
        demo_id=demo_id,
        case_id=case_id,
        visibility=body.visibility,
        status="published" if published else "pending_review",
        title=body.title,
        body=body.body,
        tags=list(body.tags),
        pattern_tags=list(pattern_tags or []),
        password_hash=hash_password(body.password) if body.visibility == "password_protected" else None,
        published_at=utcnow() if published else None,
    )


def _created(post: CasePost) -> JSONResponse:
    return JSONResponse(
        status_code=201,
        content={
            "id": str(post.id),
            "status": post.status,
            "visibility": post.visibility,
            "created_at": ser.iso(post.created_at),
        },
    )


@router.post("/cases/{case_id}/posts", status_code=201)
def create_case_post(
    case_id: str, body: PostIn, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)
):
    case = load_case(db, demo_id, case_id)
    if case.status != "closed":
        raise invalid_state("종결한 사건만 게시판에 공유할 수 있어요.")
    if needs_support(body.title, body.body, *body.tags):
        return support_payload()
    post = _new_post(demo_id, body, case.id, case.pattern_tags)
    db.add(post)
    db.commit()
    db.refresh(post)
    return _created(post)


@router.post("/board/posts", status_code=201)
def create_post(body: PostIn, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)):
    """(확장) 사건 없이 게시판에서 바로 글쓰기."""
    if needs_support(body.title, body.body, *body.tags):
        return support_payload()
    post = _new_post(demo_id, body)
    db.add(post)
    db.commit()
    db.refresh(post)
    return _created(post)


def _counts(db: Session, ids: list[uuid.UUID]) -> tuple[dict, dict]:
    if not ids:
        return {}, {}
    likes = dict(db.execute(select(PostLike.post_id, func.count()).where(PostLike.post_id.in_(ids)).group_by(PostLike.post_id)).all())
    comments = dict(
        db.execute(select(PostComment.post_id, func.count()).where(PostComment.post_id.in_(ids)).group_by(PostComment.post_id)).all()
    )
    return likes, comments


def _items(db: Session, posts: list[CasePost], viewer: uuid.UUID) -> list[dict]:
    likes, comments = _counts(db, [p.id for p in posts])
    return [ser.post_item(p, viewer, likes.get(p.id, 0), comments.get(p.id, 0)) for p in posts]


@router.get("/board/posts")
def list_posts(
    tag: list[str] | None = Query(default=None),
    q: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
    db: Session = Depends(get_db),
    demo_id: uuid.UUID = Depends(demo_id_dep),
):
    limit = clamp_limit(limit)
    tags = [t.strip() for t in (tag or []) if t and t.strip()]
    if any(len(t) > 24 for t in tags):
        raise ApiError(422, "VALIDATION_ERROR", "tag 는 1~24자여야 합니다.")
    query = (q or "").strip()
    if q is not None and not 1 <= len(query) <= 100:
        raise ApiError(422, "VALIDATION_ERROR", "q 는 1~100자여야 합니다.")

    rows = list(
        db.scalars(select(CasePost).where(CasePost.visibility == "public", CasePost.status == "published")).all()
    )
    if tags:
        rows = [p for p in rows if set(tags) & (set(p.tags or []) | set(p.pattern_tags or []))]
    if query:
        lowered = query.lower()
        rows = [p for p in rows if lowered in p.title.lower() or lowered in p.body.lower()]

    key = lambda p: (ser.iso(p.published_at or p.created_at), str(p.id))  # noqa: E731
    rows.sort(key=key, reverse=True)
    page, next_cursor = paginate(rows, key, cursor, limit)
    return {"items": _items(db, page, demo_id), "next_cursor": next_cursor}


@router.get("/board/posts/mine")
def list_my_posts(
    limit: int | None = None,
    cursor: str | None = None,
    db: Session = Depends(get_db),
    demo_id: uuid.UUID = Depends(demo_id_dep),
):
    limit = clamp_limit(limit)
    rows = list(db.scalars(select(CasePost).where(CasePost.demo_id == demo_id)).all())
    key = lambda p: (ser.iso(p.created_at), str(p.id))  # noqa: E731
    rows.sort(key=key, reverse=True)
    page, next_cursor = paginate(rows, key, cursor, limit)
    return {"items": _items(db, page, demo_id), "next_cursor": next_cursor}


def _get_post(db: Session, post_id: str) -> CasePost:
    pid = parse_uuid(post_id, NOT_FOUND_MSG)
    post = db.scalars(
        select(CasePost)
        .where(CasePost.id == pid)
        .options(selectinload(CasePost.likes), selectinload(CasePost.comments))
        .execution_options(populate_existing=True)
    ).first()
    if post is None:
        raise not_found(NOT_FOUND_MSG)
    return post


def _viewable(post: CasePost, viewer: uuid.UUID) -> bool:
    """작성자는 모든 상태를, 다른 방문자는 게시된 공개 글만 본다."""
    return post.demo_id == viewer or (post.visibility == "public" and post.status == "published")


def _interactable(post: CasePost, viewer: uuid.UUID) -> bool:
    """공감·댓글: 게시된 글(비밀번호로 연 보호 글 포함) 또는 내 글."""
    return post.demo_id == viewer or post.status == "published"


@router.get("/board/posts/{post_id}")
def get_post(post_id: str, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)):
    post = _get_post(db, post_id)
    if not _viewable(post, demo_id):
        raise not_found(NOT_FOUND_MSG)
    return ser.post_detail(post, demo_id)


@router.post("/board/posts/{post_id}/unlock")
def unlock_post(
    post_id: str,
    body: UnlockIn,
    request: Request,
    db: Session = Depends(get_db),
    demo_id: uuid.UUID = Depends(demo_id_dep),
):
    if not limiter.hit(f"unlock:{demo_id}:{post_id}", settings.unlock_limit_per_minute):
        raise ApiError(429, "RATE_LIMITED", "비밀번호를 너무 많이 시도했어요. 잠시 후 다시 시도해 주세요.")
    headers = {"Cache-Control": "no-store"}
    post = _get_post(db, post_id)
    ok = (
        post.visibility == "password_protected"
        and (post.status == "published" or post.demo_id == demo_id)
        and verify_password(post.password_hash, body.password)
    )
    if not ok:
        return error_response(request, 404, "NOT_FOUND", NOT_FOUND_MSG, headers=headers)
    return JSONResponse(content=ser.post_detail(post, demo_id), headers=headers)


@router.post("/board/posts/{post_id}/reports", status_code=201)
def report_post(
    post_id: str, body: ReportIn, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)
):
    post = _get_post(db, post_id)
    if post.status != "published":
        raise not_found(NOT_FOUND_MSG)
    existing = db.get(PostReport, (post.id, demo_id))
    if existing:
        return JSONResponse(status_code=200, content={"post_id": str(post.id), "reason": existing.reason, "created": False})
    db.add(PostReport(post_id=post.id, reporter_demo_id=demo_id, reason=body.reason))
    db.commit()
    return JSONResponse(status_code=201, content={"post_id": str(post.id), "reason": body.reason, "created": True})


@router.delete("/board/posts/{post_id}", status_code=204)
def delete_post(post_id: str, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)):
    post = _get_post(db, post_id)
    if post.demo_id != demo_id:
        raise not_found(NOT_FOUND_MSG)
    db.delete(post)
    db.commit()
    return Response(status_code=204)


@router.post("/board/posts/{post_id}/like")
def toggle_like(post_id: str, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)):
    """(확장) 공감 토글."""
    post = _get_post(db, post_id)
    if not _interactable(post, demo_id):
        raise not_found(NOT_FOUND_MSG)
    existing = db.get(PostLike, (post.id, demo_id))
    if existing:
        db.delete(existing)
        liked = False
    else:
        db.add(PostLike(post_id=post.id, demo_id=demo_id))
        liked = True
    db.commit()
    count = db.scalar(select(func.count()).select_from(PostLike).where(PostLike.post_id == post.id))
    return {"is_liked": liked, "like_count": count}


@router.post("/board/posts/{post_id}/comments", status_code=201)
def add_comment(
    post_id: str, body: CommentIn, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)
):
    """(확장) 댓글 작성."""
    post = _get_post(db, post_id)
    if not _interactable(post, demo_id):
        raise not_found(NOT_FOUND_MSG)
    if needs_support(body.content):
        return support_payload()
    comment = PostComment(post_id=post.id, demo_id=demo_id, content=body.content)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return JSONResponse(
        status_code=201,
        content={
            "id": str(comment.id),
            "author": "익명",
            "content": comment.content,
            "created_at": ser.iso(comment.created_at),
            "is_mine": True,
        },
    )
