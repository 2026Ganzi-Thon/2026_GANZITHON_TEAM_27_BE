"""Anonymous community posts, comments, reactions, and protected case sharing."""

import os
from typing import Annotated, Any, Literal
from uuid import UUID

from anyio import CapacityLimiter, to_thread
from argon2 import PasswordHasher, Type
from argon2.exceptions import HashingError, InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints, model_validator
from psycopg import AsyncConnection

from app.common import (
    APIError,
    DemoId,
    decode_cursor,
    encode_cursor,
    get_db,
    needs_support,
    support_response,
)
from app.database import Database

router = APIRouter(prefix="/api/v1", tags=["board"])

# Each operation uses roughly 19 MiB. Bound aggregate work across concurrent requests.
password_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
password_capacity = CapacityLimiter(4)
Db = Annotated[Database, Depends(get_db)]
PageSize = Annotated[int, Query(ge=1, le=50)]
Cursor = Annotated[str | None, Query(max_length=512)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=24)]
Password = Annotated[SecretStr, Field(min_length=8, max_length=64)]
PostTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
PostBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
CommentBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]

# SQL identifiers and fragments below are constants; all request values are parameters.
# Never expose the stable demo ID or the linked private case ID to other readers.
POST_FIELDS = """
    p.id, p.title, p.body, p.tags, p.visibility, p.status, p.created_at, p.published_at,
    '익명'::text AS author_label,
    p.demo_id = %(viewer)s AS is_mine,
    (SELECT count(*) FROM court_demo.post_likes l WHERE l.post_id = p.id) AS like_count,
    (SELECT count(*) FROM court_demo.post_comments c WHERE c.post_id = p.id) AS comment_count,
    EXISTS (SELECT 1 FROM court_demo.post_likes l
            WHERE l.post_id = p.id AND l.demo_id = %(viewer)s) AS liked_by_me,
    EXISTS (SELECT 1 FROM court_demo.post_bookmarks b
            WHERE b.post_id = p.id AND b.demo_id = %(viewer)s) AS bookmarked_by_me
"""
# 목록에서 남의 비공개(password_protected) 글은 제목·본문·태그를 가리고 잠금 행으로만 노출한다.
FEED_POST_FIELDS = """
    p.id,
    CASE WHEN p.visibility = 'password_protected' AND p.demo_id <> %(viewer)s
         THEN NULL ELSE p.title END AS title,
    CASE WHEN p.visibility = 'password_protected' AND p.demo_id <> %(viewer)s
         THEN NULL ELSE p.body END AS body,
    CASE WHEN p.visibility = 'password_protected' AND p.demo_id <> %(viewer)s
         THEN '{}'::text[] ELSE p.tags END AS tags,
    p.visibility, p.status, p.created_at, p.published_at,
    '익명'::text AS author_label,
    p.demo_id = %(viewer)s AS is_mine,
    (SELECT count(*) FROM court_demo.post_likes l WHERE l.post_id = p.id) AS like_count,
    (SELECT count(*) FROM court_demo.post_comments c WHERE c.post_id = p.id) AS comment_count,
    EXISTS (SELECT 1 FROM court_demo.post_likes l
            WHERE l.post_id = p.id AND l.demo_id = %(viewer)s) AS liked_by_me,
    EXISTS (SELECT 1 FROM court_demo.post_bookmarks b
            WHERE b.post_id = p.id AND b.demo_id = %(viewer)s) AS bookmarked_by_me
"""
COMMENT_FIELDS = """
    c.id, c.post_id, c.body, c.created_at, c.updated_at,
    '익명'::text AS author_label,
    c.demo_id = %(viewer)s AS is_mine,
    (SELECT count(*) FROM court_demo.comment_likes l WHERE l.comment_id = c.id) AS like_count,
    EXISTS (SELECT 1 FROM court_demo.comment_likes l
            WHERE l.comment_id = c.id AND l.demo_id = %(viewer)s) AS liked_by_me
"""


class CreatePost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visibility: Literal["public", "password_protected"]
    title: PostTitle
    body: PostBody
    tags: list[Tag] = Field(default_factory=list, max_length=5)
    password: Password | None = None

    @model_validator(mode="after")
    def validate_password(self) -> "CreatePost":
        if self.visibility == "password_protected" and self.password is None:
            raise ValueError("비밀번호 보호 글에는 비밀번호가 필요합니다.")
        if self.visibility == "public" and self.password is not None:
            raise ValueError("공개 글에는 비밀번호를 설정할 수 없습니다.")
        return self


class EditPost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: PostTitle
    body: PostBody
    tags: list[Tag] | None = Field(default=None, max_length=5)


class WriteComment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: CommentBody


class UnlockPost(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: Password


class ReportPost(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Literal["personal_information", "harmful_content", "other"]


def post_not_found() -> APIError:
    return APIError(404, "POST_NOT_FOUND", "게시글을 찾을 수 없습니다.")


def comment_not_found() -> APIError:
    return APIError(404, "COMMENT_NOT_FOUND", "댓글을 찾을 수 없습니다.")


def _page(rows: list[dict[str, Any]], limit: int, sort_field: str) -> dict[str, Any]:
    items = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = encode_cursor(last[sort_field], last["id"])
    return {"items": items, "next_cursor": next_cursor}


async def _require_closed_case(
    connection: AsyncConnection[dict[str, Any]], case_id: UUID, demo_id: UUID,
) -> None:
    row = await (
        await connection.execute(
            "SELECT status FROM court_demo.cases WHERE id = %s AND demo_id = %s FOR SHARE",
            (case_id, demo_id),
        )
    ).fetchone()
    if row is None:
        raise APIError(404, "CASE_NOT_FOUND", "사건을 찾을 수 없습니다.")
    if row["status"] != "closed":
        raise APIError(409, "INVALID_CASE_STATE", "종결한 사건만 게시할 수 있습니다.")


async def _create_post(payload: CreatePost, demo_id: UUID, db: Database, case_id: UUID | None):
    # Check access before doing password hashing and re-check when saving.
    if case_id is not None:
        async with db.connection() as connection:
            await _require_closed_case(connection, case_id, demo_id)
    if needs_support(payload.title, payload.body, *payload.tags):
        return support_response()
    password_hash = None
    if payload.password is not None:
        try:
            password_hash = await to_thread.run_sync(
                password_hasher.hash,
                payload.password.get_secret_value(),
                limiter=password_capacity,
            )
        except HashingError:
            raise APIError(503, "PASSWORD_UNAVAILABLE", "비밀번호를 처리할 수 없습니다.") from None

    auto_publish = os.getenv("BOARD_AUTO_PUBLISH", "true").strip().lower() in ("1", "true", "yes")
    initial_status = "published" if auto_publish else "pending_review"

    async with db.connection() as connection:
        if case_id is not None:
            await _require_closed_case(connection, case_id, demo_id)
        cursor = await connection.execute(
            """
            INSERT INTO court_demo.case_posts
                (case_id, demo_id, visibility, status, title, body, tags, password_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, status
            """,
            (
                case_id, demo_id, payload.visibility, initial_status, payload.title, payload.body,
                list(dict.fromkeys(payload.tags)), password_hash,
            ),
        )
        return await cursor.fetchone()


@router.post("/cases/{case_id}/posts", status_code=201)
async def create_post(case_id: UUID, payload: CreatePost, demo_id: DemoId, db: Db):
    return await _create_post(payload, demo_id, db, case_id)


@router.post("/board/posts", status_code=201)
async def create_standalone_post(payload: CreatePost, demo_id: DemoId, db: Db):
    return await _create_post(payload, demo_id, db, None)


@router.get("/board/posts")
async def list_posts(
    demo_id: DemoId,
    db: Db,
    tag: Annotated[Tag | None, Query()] = None,
    q: Annotated[
        str | None,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
        Query(),
    ] = None,
    limit: PageSize = 20,
    cursor: Cursor = None,
):
    conditions = ["p.status = 'published'"]
    parameters: dict[str, Any] = {"viewer": demo_id, "limit": limit + 1}
    if tag is not None or q is not None:
        # 태그·검색어로는 공개 글만 매칭해 비공개 글 내용이 추측되지 않게 한다.
        conditions.append("p.visibility = 'public'")
    if tag is not None:
        conditions.append("p.tags @> ARRAY[%(tag)s]::text[]")
        parameters["tag"] = tag
    if q is not None:
        conditions.append(
            "(strpos(lower(p.title), lower(%(query)s)) > 0 OR strpos(lower(p.body), lower(%(query)s)) > 0)"
        )
        parameters["query"] = q
    if cursor is not None:
        conditions.append("(p.published_at, p.id) < (%(when)s, %(cursor_id)s)")
        parameters["when"], parameters["cursor_id"] = decode_cursor(cursor)
    where = " AND ".join(conditions)
    async with db.connection() as connection:
        result = await connection.execute(
            f"""
            SELECT {FEED_POST_FIELDS} FROM court_demo.case_posts p
            WHERE {where}
            ORDER BY p.published_at DESC, p.id DESC LIMIT %(limit)s
            """,
            parameters,
        )
        return _page(await result.fetchall(), limit, "published_at")


# Register this fixed path before the UUID path.
@router.get("/board/posts/mine")
async def list_own_posts(demo_id: DemoId, db: Db, limit: PageSize = 20, cursor: Cursor = None):
    conditions = ["p.demo_id = %(viewer)s"]
    parameters: dict[str, Any] = {"viewer": demo_id, "limit": limit + 1}
    if cursor is not None:
        conditions.append("(p.created_at, p.id) < (%(when)s, %(cursor_id)s)")
        parameters["when"], parameters["cursor_id"] = decode_cursor(cursor)
    where = " AND ".join(conditions)
    async with db.connection() as connection:
        result = await connection.execute(
            f"""
            SELECT {POST_FIELDS} FROM court_demo.case_posts p
            WHERE {where}
            ORDER BY p.created_at DESC, p.id DESC LIMIT %(limit)s
            """,
            parameters,
        )
        return _page(await result.fetchall(), limit, "created_at")


@router.get("/board/bookmarks")
async def list_bookmarks(demo_id: DemoId, db: Db, limit: PageSize = 20, cursor: Cursor = None):
    conditions = ["b.demo_id = %(viewer)s", "p.visibility = 'public'", "p.status = 'published'"]
    parameters: dict[str, Any] = {"viewer": demo_id, "limit": limit + 1}
    if cursor is not None:
        conditions.append("(b.created_at, p.id) < (%(when)s, %(cursor_id)s)")
        parameters["when"], parameters["cursor_id"] = decode_cursor(cursor)
    where = " AND ".join(conditions)
    async with db.connection() as connection:
        result = await connection.execute(
            f"""
            SELECT {POST_FIELDS}, b.created_at AS bookmarked_at
            FROM court_demo.post_bookmarks b JOIN court_demo.case_posts p ON p.id = b.post_id
            WHERE {where}
            ORDER BY b.created_at DESC, p.id DESC LIMIT %(limit)s
            """,
            parameters,
        )
        return _page(await result.fetchall(), limit, "bookmarked_at")


@router.get("/board/posts/{post_id}")
async def get_post(post_id: UUID, demo_id: DemoId, db: Db):
    async with db.connection() as connection:
        row = await (
            await connection.execute(
                f"""
                SELECT {POST_FIELDS} FROM court_demo.case_posts p
                WHERE p.id = %(post_id)s
                  AND ((p.visibility = 'public' AND p.status = 'published') OR p.demo_id = %(viewer)s)
                """,
                {"post_id": post_id, "viewer": demo_id},
            )
        ).fetchone()
    if row is None:
        raise post_not_found()
    return row


@router.put("/board/posts/{post_id}")
async def edit_post(post_id: UUID, payload: EditPost, demo_id: DemoId, db: Db):
    async with db.connection() as connection:
        row = await (
            await connection.execute(
                "SELECT id FROM court_demo.case_posts WHERE id = %s AND demo_id = %s FOR UPDATE",
                (post_id, demo_id),
            )
        ).fetchone()
        if row is None:
            raise post_not_found()
        if needs_support(payload.title, payload.body, *(payload.tags or [])):
            return support_response()
        auto_publish = os.getenv("BOARD_AUTO_PUBLISH", "true").strip().lower() in ("1", "true", "yes")
        next_status = "published" if auto_publish else "pending_review"
        result = await connection.execute(
            """
            UPDATE court_demo.case_posts
            SET title = %s, body = %s, tags = coalesce(%s::text[], tags), status = %s
            WHERE id = %s
            RETURNING id, status
            """,
            (payload.title, payload.body,
             list(dict.fromkeys(payload.tags)) if payload.tags is not None else None,
             next_status, post_id),
        )
        return await result.fetchone()


async def _require_public_post(
    connection: AsyncConnection[dict[str, Any]], post_id: UUID, *, for_update: bool = False,
) -> None:
    lock = "UPDATE" if for_update else "SHARE"
    row = await (
        await connection.execute(
            f"""
            SELECT id FROM court_demo.case_posts
            WHERE id = %s AND visibility = 'public' AND status = 'published' FOR {lock}
            """,
            (post_id,),
        )
    ).fetchone()
    if row is None:
        raise post_not_found()


async def _require_public_comment(
    connection: AsyncConnection[dict[str, Any]], comment_id: UUID, *, owner: UUID | None = None,
) -> dict[str, Any]:
    row = await (
        await connection.execute(
            "SELECT post_id FROM court_demo.post_comments WHERE id = %s", (comment_id,),
        )
    ).fetchone()
    if row is None:
        raise comment_not_found()
    # Always lock the parent before the comment to keep edit/delete/reaction lock order consistent.
    try:
        await _require_public_post(connection, row["post_id"])
    except APIError as exc:
        if exc.code == "POST_NOT_FOUND":
            raise comment_not_found() from None
        raise
    comment = await (
        await connection.execute(
            "SELECT id, post_id, demo_id FROM court_demo.post_comments WHERE id = %s FOR UPDATE",
            (comment_id,),
        )
    ).fetchone()
    if comment is None or (owner is not None and comment["demo_id"] != owner):
        raise comment_not_found()
    return comment


async def _read_comment(
    connection: AsyncConnection[dict[str, Any]], comment_id: UUID, demo_id: UUID,
) -> dict[str, Any]:
    row = await (
        await connection.execute(
            f"SELECT {COMMENT_FIELDS} FROM court_demo.post_comments c WHERE c.id = %(comment_id)s",
            {"comment_id": comment_id, "viewer": demo_id},
        )
    ).fetchone()
    if row is None:
        raise comment_not_found()
    return row


@router.get("/board/posts/{post_id}/comments")
async def list_comments(
    post_id: UUID, demo_id: DemoId, db: Db, limit: PageSize = 20, cursor: Cursor = None,
):
    conditions = ["c.post_id = %(post_id)s"]
    parameters: dict[str, Any] = {"post_id": post_id, "viewer": demo_id, "limit": limit + 1}
    if cursor is not None:
        conditions.append("(c.created_at, c.id) > (%(when)s, %(cursor_id)s)")
        parameters["when"], parameters["cursor_id"] = decode_cursor(cursor)
    where = " AND ".join(conditions)
    async with db.connection() as connection:
        await _require_public_post(connection, post_id)
        result = await connection.execute(
            f"""
            SELECT {COMMENT_FIELDS} FROM court_demo.post_comments c WHERE {where}
            ORDER BY c.created_at ASC, c.id ASC LIMIT %(limit)s
            """,
            parameters,
        )
        return _page(await result.fetchall(), limit, "created_at")


@router.post("/board/posts/{post_id}/comments", status_code=201)
async def create_comment(post_id: UUID, payload: WriteComment, demo_id: DemoId, db: Db):
    async with db.connection() as connection:
        await _require_public_post(connection, post_id)
        if needs_support(payload.body):
            return support_response()
        row = await (
            await connection.execute(
                "INSERT INTO court_demo.post_comments (post_id, demo_id, body) VALUES (%s, %s, %s) RETURNING id",
                (post_id, demo_id, payload.body),
            )
        ).fetchone()
        return await _read_comment(connection, row["id"], demo_id)


@router.put("/board/comments/{comment_id}")
async def edit_comment(comment_id: UUID, payload: WriteComment, demo_id: DemoId, db: Db):
    async with db.connection() as connection:
        await _require_public_comment(connection, comment_id, owner=demo_id)
        if needs_support(payload.body):
            return support_response()
        await connection.execute(
            "UPDATE court_demo.post_comments SET body = %s, updated_at = statement_timestamp() WHERE id = %s",
            (payload.body, comment_id),
        )
        return await _read_comment(connection, comment_id, demo_id)


@router.delete("/board/comments/{comment_id}", status_code=204)
async def delete_comment(comment_id: UUID, demo_id: DemoId, db: Db) -> Response:
    async with db.connection() as connection:
        await _require_public_comment(connection, comment_id, owner=demo_id)
        await connection.execute("DELETE FROM court_demo.post_comments WHERE id = %s", (comment_id,))
    return Response(status_code=204)


async def _post_like(post_id: UUID, demo_id: UUID, db: Database, liked: bool) -> dict[str, Any]:
    async with db.connection() as connection:
        await _require_public_post(connection, post_id, for_update=True)
        if liked:
            await connection.execute(
                "INSERT INTO court_demo.post_likes (post_id, demo_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (post_id, demo_id),
            )
        else:
            await connection.execute(
                "DELETE FROM court_demo.post_likes WHERE post_id = %s AND demo_id = %s", (post_id, demo_id),
            )
        row = await (
            await connection.execute(
                "SELECT count(*) AS like_count FROM court_demo.post_likes WHERE post_id = %s", (post_id,),
            )
        ).fetchone()
        return {"liked": liked, "like_count": row["like_count"]}


@router.put("/board/posts/{post_id}/like")
async def like_post(post_id: UUID, demo_id: DemoId, db: Db):
    return await _post_like(post_id, demo_id, db, True)


@router.delete("/board/posts/{post_id}/like")
async def unlike_post(post_id: UUID, demo_id: DemoId, db: Db):
    return await _post_like(post_id, demo_id, db, False)


async def _comment_like(comment_id: UUID, demo_id: UUID, db: Database, liked: bool) -> dict[str, Any]:
    async with db.connection() as connection:
        await _require_public_comment(connection, comment_id)
        if liked:
            await connection.execute(
                "INSERT INTO court_demo.comment_likes (comment_id, demo_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (comment_id, demo_id),
            )
        else:
            await connection.execute(
                "DELETE FROM court_demo.comment_likes WHERE comment_id = %s AND demo_id = %s",
                (comment_id, demo_id),
            )
        row = await (
            await connection.execute(
                "SELECT count(*) AS like_count FROM court_demo.comment_likes WHERE comment_id = %s", (comment_id,),
            )
        ).fetchone()
        return {"liked": liked, "like_count": row["like_count"]}


@router.put("/board/comments/{comment_id}/like")
async def like_comment(comment_id: UUID, demo_id: DemoId, db: Db):
    return await _comment_like(comment_id, demo_id, db, True)


@router.delete("/board/comments/{comment_id}/like")
async def unlike_comment(comment_id: UUID, demo_id: DemoId, db: Db):
    return await _comment_like(comment_id, demo_id, db, False)


async def _bookmark(post_id: UUID, demo_id: UUID, db: Database, bookmarked: bool) -> dict[str, bool]:
    async with db.connection() as connection:
        await _require_public_post(connection, post_id, for_update=True)
        if bookmarked:
            await connection.execute(
                "INSERT INTO court_demo.post_bookmarks (post_id, demo_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (post_id, demo_id),
            )
        else:
            await connection.execute(
                "DELETE FROM court_demo.post_bookmarks WHERE post_id = %s AND demo_id = %s", (post_id, demo_id),
            )
    return {"bookmarked": bookmarked}


@router.put("/board/posts/{post_id}/bookmark")
async def bookmark_post(post_id: UUID, demo_id: DemoId, db: Db):
    return await _bookmark(post_id, demo_id, db, True)


@router.delete("/board/posts/{post_id}/bookmark")
async def unbookmark_post(post_id: UUID, demo_id: DemoId, db: Db):
    return await _bookmark(post_id, demo_id, db, False)


async def _consume_unlock_attempt(
    connection: AsyncConnection[dict[str, Any]], post_id: UUID, bucket: str, maximum: int,
) -> None:
    row = await (
        await connection.execute(
            """
            INSERT INTO court_demo.board_unlock_attempts AS rate
                (post_id, bucket, window_started_at, attempts)
            VALUES (%s, %s, statement_timestamp(), 1)
            ON CONFLICT (post_id, bucket) DO UPDATE
            SET window_started_at = CASE
                    WHEN rate.window_started_at <= statement_timestamp() - interval '5 minutes'
                    THEN statement_timestamp() ELSE rate.window_started_at END,
                attempts = CASE
                    WHEN rate.window_started_at <= statement_timestamp() - interval '5 minutes'
                    THEN 1 ELSE rate.attempts + 1 END
            WHERE rate.window_started_at <= statement_timestamp() - interval '5 minutes'
               OR rate.attempts < %s
            RETURNING attempts
            """,
            (post_id, bucket, maximum),
        )
    ).fetchone()
    if row is None:
        raise APIError(429, "UNLOCK_RATE_LIMITED", "비밀번호 확인 횟수를 초과했습니다. 잠시 후 다시 시도해 주세요.")


@router.post("/board/posts/{post_id}/unlock")
async def unlock_post(post_id: UUID, payload: UnlockPost, response: Response, demo_id: DemoId, db: Db):
    response.headers["Cache-Control"] = "no-store"
    async with db.connection() as connection:
        row = await (
            await connection.execute(
                """
                SELECT password_hash FROM court_demo.case_posts
                WHERE id = %s AND visibility = 'password_protected' AND status = 'published'
                FOR SHARE
                """,
                (post_id,),
            )
        ).fetchone()
        if row is None:
            raise post_not_found()
        # The shared budget prevents generating new demo IDs to bypass the limit.
        # Reserve both budgets before verification so simultaneous attempts count.
        await _consume_unlock_attempt(connection, post_id, "global", 30)
        await connection.execute(
            """
            DELETE FROM court_demo.board_unlock_attempts
            WHERE post_id = %s AND bucket <> 'global'
              AND window_started_at <= statement_timestamp() - interval '5 minutes'
            """,
            (post_id,),
        )
        await _consume_unlock_attempt(connection, post_id, str(demo_id), 5)
        password_hash = row["password_hash"]

    try:
        verified = await to_thread.run_sync(
            password_hasher.verify,
            password_hash,
            payload.password.get_secret_value(),
            limiter=password_capacity,
        )
    except (VerificationError, InvalidHashError):
        raise post_not_found() from None
    if not verified:
        raise post_not_found()

    # Visibility or the password may change during verification.
    async with db.connection() as connection:
        row = await (
            await connection.execute(
                f"""
                SELECT {POST_FIELDS} FROM court_demo.case_posts p
                WHERE p.id = %(post_id)s AND p.visibility = 'password_protected'
                  AND p.status = 'published' AND p.password_hash = %(password_hash)s
                """,
                {"post_id": post_id, "password_hash": password_hash, "viewer": demo_id},
            )
        ).fetchone()
    if row is None:
        raise post_not_found()
    return row


@router.post("/board/posts/{post_id}/reports", status_code=201)
async def report_post(post_id: UUID, payload: ReportPost, response: Response, demo_id: DemoId, db: Db):
    async with db.connection() as connection:
        await _require_public_post(connection, post_id)
        report = await connection.execute(
            """
            INSERT INTO court_demo.post_reports (post_id, reporter_demo_id, reason)
            VALUES (%s, %s, %s)
            ON CONFLICT (post_id, reporter_demo_id) DO NOTHING
            """,
            (post_id, demo_id, payload.reason),
        )
        if report.rowcount == 0:
            response.status_code = 200
    return {"reported": True}


@router.delete("/board/posts/{post_id}", status_code=204)
async def delete_post(post_id: UUID, demo_id: DemoId, db: Db) -> Response:
    async with db.connection() as connection:
        result = await connection.execute(
            """
            DELETE FROM court_demo.case_posts WHERE id = %s AND demo_id = %s
            """,
            (post_id, demo_id),
        )
        if result.rowcount == 0:
            raise post_not_found()
    return Response(status_code=204)
