"""사건 접수·목록·상세·통계·삭제."""

import hashlib
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import serializers as ser
from app.db import get_db
from app.deps import demo_id_dep
from app.errors import ApiError, conflict, not_found
from app.models import CASE_STATUSES, Case, IdempotencyKey, utcnow
from app.safety import needs_support, support_payload
from app.schemas import CreateCaseIn
from app.utils import clamp_limit, paginate, parse_uuid

router = APIRouter(prefix="/cases", tags=["cases"])

KST = ZoneInfo("Asia/Seoul")
IDEMPOTENCY_TTL = timedelta(hours=24)
PATTERNS = (
    "all_or_nothing", "overgeneralization", "mind_reading", "catastrophizing", "labeling", "discounting_positives"
)


def _load_case(db: Session, demo_id: uuid.UUID, case_id: str, for_update: bool = False) -> Case:
    cid = parse_uuid(case_id, "사건을 찾을 수 없어요.")
    stmt = (
        select(Case)
        .where(Case.id == cid, Case.demo_id == demo_id)
        .options(selectinload(Case.evidence), selectinload(Case.turns), selectinload(Case.decision))
        # AI 호출 뒤 다시 읽을 때 세션 캐시가 아닌 DB 최신 값을 쓰도록 한다.
        .execution_options(populate_existing=True)
    )
    if for_update and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    case = db.scalars(stmt).first()
    if case is None:
        raise not_found("사건을 찾을 수 없어요.")
    return case


def load_case(db: Session, demo_id: uuid.UUID, case_id: str, for_update: bool = False) -> Case:
    return _load_case(db, demo_id, case_id, for_update)


def _next_case_number(db: Session, demo_id: uuid.UUID) -> str:
    year = datetime.now(KST).year
    prefix = f"{year}-"
    numbers = db.scalars(
        select(Case.case_number).where(Case.demo_id == demo_id, Case.case_number.like(prefix + "%"))
    ).all()
    seq = max((int(n.split("-", 1)[1]) for n in numbers if n.split("-", 1)[1].isdigit()), default=0) + 1
    return f"{prefix}{seq:03d}"


def _request_hash(body: CreateCaseIn) -> str:
    canonical = json.dumps(body.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


@router.post("", status_code=201)
def create_case(
    body: CreateCaseIn,
    db: Session = Depends(get_db),
    demo_id: uuid.UUID = Depends(demo_id_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    key = (idempotency_key or "").strip()[:255] or None
    req_hash = _request_hash(body)

    if key:
        saved = db.get(IdempotencyKey, (demo_id, "create_case", key))
        if saved and ser.aware(saved.expires_at) > utcnow():
            if saved.request_hash != req_hash:
                raise conflict("IDEMPOTENCY_MISMATCH", "같은 Idempotency-Key 로 다른 내용을 보냈어요.")
            return JSONResponse(status_code=saved.response_status, content=saved.response_body)
        if saved:  # 만료된 키는 지우고 새로 처리
            db.delete(saved)
            db.commit()

    if needs_support(body.thought):
        payload = support_payload()
        status = 200
        case_id = None
    else:
        for _ in range(3):
            case = Case(
                demo_id=demo_id,
                case_number=_next_case_number(db, demo_id),
                status="received",
                original_thought=body.thought,
                emotion_before_type=body.emotion.type if body.emotion else None,
                emotion_before_intensity=body.emotion.intensity if body.emotion else None,
                pattern_candidates=[],
                pattern_tags=[],
            )
            db.add(case)
            try:
                db.flush()
                break
            except IntegrityError:
                db.rollback()
        else:
            raise ApiError(503, "DB_UNAVAILABLE", "사건번호를 만들지 못했어요. 다시 시도해 주세요.")
        db.refresh(case)
        payload = {"outcome": "case_created", "case": ser.case_brief(case)}
        status = 201
        case_id = case.id

    if key:
        db.add(
            IdempotencyKey(
                demo_id=demo_id,
                scope="create_case",
                key=key,
                request_hash=req_hash,
                case_id=case_id,
                response_status=status,
                response_body=payload,
                expires_at=utcnow() + IDEMPOTENCY_TTL,
            )
        )
    db.commit()
    return JSONResponse(status_code=status, content=payload)


@router.get("")
def list_cases(
    status: str | None = None,
    pattern: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
    db: Session = Depends(get_db),
    demo_id: uuid.UUID = Depends(demo_id_dep),
):
    limit = clamp_limit(limit)
    if status and status not in CASE_STATUSES:
        raise ApiError(422, "VALIDATION_ERROR", "status 값이 올바르지 않습니다.")
    if pattern and pattern not in PATTERNS:
        raise ApiError(422, "VALIDATION_ERROR", "pattern 값이 올바르지 않습니다.")

    stmt = select(Case).where(Case.demo_id == demo_id).options(selectinload(Case.decision))
    if status:
        stmt = stmt.where(Case.status == status)
    rows = list(db.scalars(stmt).all())
    if pattern:
        rows = [c for c in rows if pattern in (c.pattern_tags or [])]
    rows.sort(key=lambda c: (ser.iso(c.created_at), str(c.id)), reverse=True)
    page, next_cursor = paginate(rows, lambda c: (ser.iso(c.created_at), str(c.id)), cursor, limit)
    return {"items": [ser.case_brief(c) for c in page], "next_cursor": next_cursor}


@router.get("/stats")
def case_stats(
    from_: date = Query(alias="from"),
    to: date = Query(),
    tz: str = Query(default="Asia/Seoul", alias="timezone"),
    db: Session = Depends(get_db),
    demo_id: uuid.UUID = Depends(demo_id_dep),
):
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ApiError(422, "VALIDATION_ERROR", "timezone 값이 올바르지 않습니다.") from None
    if to <= from_:
        raise ApiError(422, "VALIDATION_ERROR", "to 는 from 보다 늦어야 합니다.")
    start = datetime.combine(from_, time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(to, time.min, tzinfo=zone).astimezone(timezone.utc)

    rows = db.scalars(select(Case).where(Case.demo_id == demo_id, Case.status == "closed")).all()
    closed = [c for c in rows if c.closed_at and start <= ser.aware(c.closed_at) < end]

    pattern_counts: dict[str, int] = {}
    for c in closed:
        for p in c.pattern_tags or []:
            pattern_counts[p] = pattern_counts.get(p, 0) + 1

    diffs = [
        c.emotion_after_intensity - c.emotion_before_intensity
        for c in closed
        if c.emotion_before_type
        and c.emotion_before_type == c.emotion_after_type
        and c.emotion_before_intensity is not None
        and c.emotion_after_intensity is not None
    ]
    return {
        "closed_count": len(closed),
        "pattern_counts": pattern_counts,
        "emotion_change": {
            "average": round(sum(diffs) / len(diffs), 1) if diffs else None,
            "count": len(diffs),
        },
    }


@router.get("/{case_id}")
def get_case(case_id: str, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)):
    return ser.case_detail(_load_case(db, demo_id, case_id))


@router.delete("/{case_id}", status_code=204)
def delete_case(case_id: str, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)):
    case = _load_case(db, demo_id, case_id)
    db.delete(case)
    db.commit()
    return Response(status_code=204)
