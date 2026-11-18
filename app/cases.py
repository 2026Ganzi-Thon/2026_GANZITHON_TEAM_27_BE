"""Exhibition case flow and archive."""

import hashlib
from datetime import date, datetime, time, timezone
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from app.common import APIError, DemoId, decode_cursor, encode_cursor, get_db, needs_support, support_response
from app.workflow import Evidence, Pattern, UpstageResponseError, invoke_workflow

router = APIRouter(prefix="/api/v1", tags=["cases"])
CaseStatus = Literal["received", "evidence_review", "defense", "decision_review", "closed", "support_required"]
DecisionType = Literal["reframed", "deferred", "action_needed"]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
ActionText = Annotated[str, StringConstraints(min_length=1, max_length=500)]
Cursor = Annotated[str | None, Query(max_length=256)]
Limit = Annotated[int, Query(ge=1, le=50)]
CaseKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]

_CASE_FIELDS = """
    id, case_number, status, original_thought, original_summary AS summary, title, claim, version,
    pattern_candidates::text[] AS pattern_candidates, pattern_tags::text[] AS pattern_tags,
    pattern_tags[1]::text AS primary_pattern,
    emotion_before_type, emotion_before_intensity, emotion_after_type, emotion_after_intensity,
    created_at, updated_at, closed_at
"""
_QUESTION_FIELDS = """
    id, sequence, question AS prompt, revision, prompts, suggested_answers,
    answer, answer_status, answered_at
"""
_DECISION_FIELDS = """
    draft_type AS type, reported_facts, unsupported_claims, suggested_statement, next_action,
    final_type, final_statement, finalized_at, follow_up_choice, follow_up_action, follow_up_updated_at
"""
_FOLLOW_UP_OPTIONS = (
    {"id": "walk", "title": "가볍게 걷기", "description": "편한 곳에서 잠깐 걸어보기", "tag": "5분"},
    {"id": "music", "title": "음악 듣기", "description": "좋아하는 음악 한 곡 듣기", "tag": "한 곡"},
    {"id": "water", "title": "잠깐 쉬기", "description": "물 한 잔 마시고 잠시 쉬기", "tag": "2분"},
    {"id": "note", "title": "한 줄 적기", "description": "다음에 확인할 일 한 가지 적기", "tag": "2분"},
)


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Emotion(Payload):
    type: Literal["anxiety", "sadness", "anger", "shame", "guilt", "other"]
    intensity: Annotated[int, Field(strict=True, ge=0, le=100)]


class Intake(Payload):
    thought: LongText
    emotion: Emotion | None = None


class ConfirmEvidence(Payload):
    claim: LongText
    pattern_tags: list[Pattern] = Field(max_length=11)
    evidence: list[Evidence] = Field(max_length=20)


class Revision(Payload):
    revision: Annotated[int, Field(strict=True, ge=0, le=2)]


class Answer(Revision):
    action: Literal["answer", "skip", "not_sure"]
    text: ShortText | None = None

    @model_validator(mode="after")
    def check_text(self):
        if (self.action == "answer") != (self.text is not None):
            raise ValueError("text is required only when action is answer")
        return self


class Rephrase(Payload):
    style: Literal["neutral", "shorter", "gentler"]
    version: Annotated[int, Field(strict=True, ge=0)]


class FinalDecision(Payload):
    decision_type: DecisionType
    final_statement: ShortText | None = None
    emotion_after: Emotion | None = None

    @model_validator(mode="after")
    def check_statement(self):
        if self.decision_type != "deferred" and self.final_statement is None:
            raise ValueError("final_statement is required for this decision type")
        return self


class FollowUp(Payload):
    choice: Literal["action", "rest"]
    action_text: ActionText | None = None

    @model_validator(mode="after")
    def check_action(self):
        if (self.choice == "action") != (self.action_text is not None):
            raise ValueError("action_text is required only when choice is action")
        return self


async def fetch_one(conn, query, values=()):
    return await (await conn.execute(query, values)).fetchone()


async def fetch_all(conn, query, values=()):
    return await (await conn.execute(query, values)).fetchall()


async def owned_case(conn, demo_id, case_id, *, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    row = await fetch_one(conn, f"""
        SELECT {_CASE_FIELDS} FROM court_demo.cases
        WHERE id = %s AND demo_id = %s{suffix}
    """, (case_id, demo_id))
    if row is None:
        raise APIError(404, "CASE_NOT_FOUND", "사건을 찾을 수 없습니다.")
    return row


def require_status(case, *statuses):
    if case["status"] not in statuses:
        raise APIError(409, "INVALID_CASE_STATE", "현재 사건 단계에서는 요청을 처리할 수 없습니다.")


def require_version(case, version):
    if case["version"] != version:
        raise APIError(409, "VERSION_CONFLICT", "사건이 변경되었습니다. 새로 조회한 뒤 다시 시도해 주세요.")


async def touch(conn, case_id, status=None):
    return await fetch_one(conn, """
        UPDATE court_demo.cases
        SET status = COALESCE(%s::court_demo.case_status, status),
            version = version + 1, updated_at = now()
        WHERE id = %s
        RETURNING status, version
    """, (status, case_id))


async def replace_evidence(conn, case_id, evidence):
    await conn.execute("DELETE FROM court_demo.case_evidence WHERE case_id = %s", (case_id,))
    if evidence:
        async with conn.cursor() as cursor:
            await cursor.executemany("""
                INSERT INTO court_demo.case_evidence (case_id, type, text, position)
                VALUES (%s, %s, %s, %s)
            """, [(case_id, e["type"], e["text"], i) for i, e in enumerate(evidence, 1)])


async def evidence_for(conn, case_id):
    return await fetch_all(conn, """
        SELECT type, text FROM court_demo.case_evidence WHERE case_id = %s ORDER BY position
    """, (case_id,))


async def turns_for(conn, case_id):
    return await fetch_all(conn, f"""
        SELECT {_QUESTION_FIELDS} FROM court_demo.defense_turns
        WHERE case_id = %s ORDER BY sequence
    """, (case_id,))


async def question_for(conn, case_id, question_id):
    row = await fetch_one(conn, f"""
        SELECT {_QUESTION_FIELDS} FROM court_demo.defense_turns
        WHERE case_id = %s AND id = %s
    """, (case_id, question_id))
    if row is None:
        raise APIError(404, "QUESTION_NOT_FOUND", "변론 질문을 찾을 수 없습니다.")
    return row


def require_pending(question, revision):
    if question["revision"] != revision:
        raise APIError(409, "STALE_REVISION", "질문 표현이 변경되었습니다. 현재 질문을 다시 확인해 주세요.")
    if question["answer_status"] != "pending":
        raise APIError(409, "QUESTION_CONFLICT", "현재 질문을 다시 확인해 주세요.")


async def decision_for(conn, case_id):
    return await fetch_one(conn, f"""
        SELECT {_DECISION_FIELDS} FROM court_demo.case_decisions WHERE case_id = %s
    """, (case_id,))


def case_detail_fields(case):
    result = dict(case)
    for phase, field in (("before", "emotion"), ("after", "emotion_after")):
        kind = case[f"emotion_{phase}_type"]
        result[field] = {"type": kind, "intensity": case[f"emotion_{phase}_intensity"]} if kind else None
    return result


async def generate(request, stage, **state):
    try:
        return (await invoke_workflow(request.app.state.workflow, {"stage": stage, **state}))["result"]
    except (ValidationError, UpstageResponseError):
        raise APIError(502, "AI_INVALID_RESPONSE", "AI 응답 형식을 확인하지 못했습니다.") from None
    except Exception:
        raise APIError(503, "AI_UNAVAILABLE", "AI 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.") from None


def context_for(case, evidence, turns):
    return {
        "thought": case["original_thought"],
        "claim": case["claim"],
        "pattern_tags": case["pattern_tags"],
        "evidence": evidence,
        "turns": [{"question": q["prompt"], "answer": q["answer"], "answer_status": q["answer_status"]}
                  for q in turns],
    }


def intake_response(status, body):
    return JSONResponse(jsonable_encoder(body), status_code=status)


@router.post("/cases", status_code=201, responses={200: {"description": "Support guidance"}})
async def intake(request: Request, demo_id: DemoId, key: CaseKey, payload: Intake):
    request_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    if needs_support(payload.thought):
        return support_response()
    async with get_db(request).connection() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           (f"case-intake:{demo_id}:{key}",))
        await conn.execute("""
            DELETE FROM court_demo.idempotency_keys
            WHERE demo_id = %s AND scope = 'case-intake' AND key = %s AND expires_at <= now()
        """, (demo_id, key))
        saved = await fetch_one(conn, """
            SELECT request_hash, response_status, response_body FROM court_demo.idempotency_keys
            WHERE demo_id = %s AND scope = 'case-intake' AND key = %s
        """, (demo_id, key))
        if saved:
            if saved["request_hash"] != request_hash:
                raise APIError(409, "IDEMPOTENCY_CONFLICT", "이 요청 키는 다른 입력에 사용되었습니다.")
            return intake_response(saved["response_status"], saved["response_body"])

        emotion = payload.emotion
        row = await fetch_one(conn, """
            INSERT INTO court_demo.cases
                (demo_id, original_thought, emotion_before_type, emotion_before_intensity)
            VALUES (%s, %s, %s, %s)
            RETURNING id, case_number, status, version, created_at
        """, (demo_id, payload.thought, emotion.type if emotion else None,
              emotion.intensity if emotion else None))
        body = jsonable_encoder({"outcome": "case_created", "case": row})
        await conn.execute("""
            INSERT INTO court_demo.idempotency_keys
                (demo_id, scope, key, request_hash, case_id, response_status, response_body, expires_at)
            VALUES (%s, 'case-intake', %s, %s, %s, %s, %s, now() + interval '24 hours')
        """, (demo_id, key, request_hash, row["id"], 201, Jsonb(body)))
        return intake_response(201, body)


@router.get("/cases")
async def list_cases(request: Request, demo_id: DemoId, status: CaseStatus | None = None,
                     pattern: Pattern | None = None, limit: Limit = 20, cursor: Cursor = None):
    when, item_id = decode_cursor(cursor) if cursor else (None, None)
    async with get_db(request).connection() as conn:
        rows = await fetch_all(conn, """
            SELECT id, case_number, status, title, pattern_tags::text[] AS pattern_tags,
                   created_at, updated_at, closed_at, version
            FROM court_demo.cases
            WHERE demo_id = %s
              AND (%s::court_demo.case_status IS NULL OR status = %s)
              AND (%s::court_demo.pattern_type IS NULL OR %s::court_demo.pattern_type = ANY(pattern_tags))
              AND (%s::timestamptz IS NULL OR (created_at, id) < (%s, %s))
            ORDER BY created_at DESC, id DESC LIMIT %s
        """, (demo_id, status, status, pattern, pattern, when, when, item_id, limit + 1))
    next_cursor = encode_cursor(rows[limit - 1]["created_at"], rows[limit - 1]["id"]) if len(rows) > limit else None
    return {"items": rows[:limit], "next_cursor": next_cursor}


@router.get("/cases/stats")
async def stats(request: Request, demo_id: DemoId,
                start: Annotated[date, Query(alias="from")], end: Annotated[date, Query(alias="to")],
                tz_name: Annotated[str, Query(alias="timezone", max_length=80)] = "Asia/Seoul"):
    if end <= start:
        raise APIError(422, "INVALID_DATE_RANGE", "종료일은 시작일보다 뒤여야 합니다.")
    try:
        tz = ZoneInfo(tz_name)
        since = datetime.combine(start, time.min, tzinfo=tz).astimezone(timezone.utc)
        until = datetime.combine(end, time.min, tzinfo=tz).astimezone(timezone.utc)
    except (ZoneInfoNotFoundError, ValueError, OverflowError):
        raise APIError(422, "INVALID_DATE_RANGE", "날짜와 시간대를 확인해 주세요.") from None
    async with get_db(request).connection() as conn:
        row = await fetch_one(conn, """
            WITH closed AS (
                SELECT pattern_tags, emotion_before_type, emotion_after_type,
                       emotion_before_intensity, emotion_after_intensity
                FROM court_demo.cases
                WHERE demo_id = %s AND status = 'closed' AND closed_at >= %s AND closed_at < %s
            )
            SELECT count(*) AS closed_count,
                   count(*) FILTER (WHERE emotion_before_type = emotion_after_type) AS compared_count,
                   avg(emotion_after_intensity - emotion_before_intensity)
                       FILTER (WHERE emotion_before_type = emotion_after_type) AS average_change,
                   (SELECT jsonb_object_agg(pattern, total) FROM (
                      SELECT tag::text AS pattern, count(*) AS total
                      FROM closed CROSS JOIN LATERAL unnest(pattern_tags) AS t(tag) GROUP BY tag
                   ) AS counts) AS pattern_counts
            FROM closed
        """, (demo_id, since, until))
    return {"closed_count": row["closed_count"], "pattern_counts": row["pattern_counts"] or {},
            "emotion_change": {"count": row["compared_count"], "average": row["average_change"]}}


@router.get("/cases/{case_id}")
async def detail(request: Request, demo_id: DemoId, case_id: UUID):
    async with get_db(request).connection() as conn:
        # One snapshot for the status, related questions and draft.
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        case = await owned_case(conn, demo_id, case_id)
        questions = await turns_for(conn, case_id)
        all_answered = bool(questions) and all(turn["answer_status"] != "pending" for turn in questions)
        return {"case": case_detail_fields(case), "evidence": await evidence_for(conn, case_id),
                "questions": questions, "decision": await decision_for(conn, case_id),
                "can_request_decision": case["status"] == "defense" and all_answered,
                "defense_completed": all_answered and len(questions) == 3}


@router.delete("/cases/{case_id}", status_code=204)
async def delete_case(request: Request, demo_id: DemoId, case_id: UUID):
    async with get_db(request).connection() as conn:
        result = await conn.execute("DELETE FROM court_demo.cases WHERE id = %s AND demo_id = %s",
                                    (case_id, demo_id))
        if not result.rowcount:
            raise APIError(404, "CASE_NOT_FOUND", "사건을 찾을 수 없습니다.")
    return Response(status_code=204)


@router.post("/cases/{case_id}/prosecution")
async def prosecution(request: Request, demo_id: DemoId, case_id: UUID):
    db = get_db(request)
    async with db.connection() as conn:
        case = await owned_case(conn, demo_id, case_id)
        if case["status"] not in ("received", "support_required") and case["title"]:
            return {"status": case["status"], "version": case["version"], "title": case["title"],
                    "claim": case["claim"], "summary": case["summary"],
                    "pattern_candidates": case["pattern_candidates"],
                    "evidence": await evidence_for(conn, case_id)}
        require_status(case, "received")
    draft = await generate(request, "prosecution", thought=case["original_thought"])
    async with db.connection() as conn:
        current = await owned_case(conn, demo_id, case_id, lock=True)
        require_version(current, case["version"])
        await conn.execute("""
            UPDATE court_demo.cases SET title = %s, claim = %s, original_summary = %s,
                pattern_candidates = %s::court_demo.pattern_type[] WHERE id = %s
        """, (draft["title"], draft["claim"], draft["summary"], draft["pattern_candidates"], case_id))
        await replace_evidence(conn, case_id, draft["evidence"])
        return {**draft, **(await touch(conn, case_id, "evidence_review"))}


@router.put("/cases/{case_id}/evidence")
async def confirm_evidence(request: Request, demo_id: DemoId, case_id: UUID, payload: ConfirmEvidence):
    async with get_db(request).connection() as conn:
        case = await owned_case(conn, demo_id, case_id, lock=True)
        require_status(case, "evidence_review")
        if needs_support(payload.claim, *(e.text for e in payload.evidence)):
            await touch(conn, case_id, "support_required")
            return support_response()
        tags = list(dict.fromkeys(payload.pattern_tags))
        evidence = [e.model_dump() for e in payload.evidence]
        await conn.execute("""
            UPDATE court_demo.cases SET claim = %s, pattern_tags = %s::court_demo.pattern_type[]
            WHERE id = %s
        """, (payload.claim, tags, case_id))
        await replace_evidence(conn, case_id, evidence)
        return {"claim": payload.claim, "pattern_tags": tags, "evidence": evidence,
                **(await touch(conn, case_id, "defense"))}


@router.post("/cases/{case_id}/defense/questions", status_code=201,
             responses={200: {"description": "Existing pending question"}})
async def next_question(request: Request, response: Response, demo_id: DemoId, case_id: UUID):
    db = get_db(request)
    async with db.connection() as conn:
        case = await owned_case(conn, demo_id, case_id)
        require_status(case, "defense")
        turns = await turns_for(conn, case_id)
        if turns and turns[-1]["answer_status"] == "pending":
            response.status_code = 200
            return turns[-1]
        if len(turns) >= 3:
            raise APIError(409, "DEFENSE_COMPLETED", "세 질문을 모두 처리했습니다. 판결을 요청해 주세요.")
        evidence = await evidence_for(conn, case_id)
    draft = await generate(request, "defense", **context_for(case, evidence, turns))
    async with db.connection() as conn:
        current = await owned_case(conn, demo_id, case_id, lock=True)
        require_version(current, case["version"])
        row = await fetch_one(conn, f"""
            INSERT INTO court_demo.defense_turns
                (case_id, sequence, question, prompts, suggested_answers)
            VALUES (%s, %s, %s, ARRAY[%s]::text[], %s) RETURNING {_QUESTION_FIELDS}
        """, (case_id, len(turns) + 1, draft["prompt"], draft["prompt"],
              Jsonb(draft["suggested_answers"])))
        await touch(conn, case_id)
        return row


@router.put("/cases/{case_id}/defense/questions/{question_id}/answer")
async def answer_question(request: Request, demo_id: DemoId, case_id: UUID, question_id: UUID, payload: Answer):
    async with get_db(request).connection() as conn:
        case = await owned_case(conn, demo_id, case_id, lock=True)
        require_status(case, "defense")
        require_pending(await question_for(conn, case_id, question_id), payload.revision)
        if needs_support(payload.text):
            await touch(conn, case_id, "support_required")
            return support_response()
        status = {"answer": "answered", "skip": "skipped", "not_sure": "not_sure"}[payload.action]
        row = await fetch_one(conn, f"""
            UPDATE court_demo.defense_turns SET answer = %s, answer_status = %s, answered_at = now()
            WHERE case_id = %s AND id = %s RETURNING {_QUESTION_FIELDS}
        """, (payload.text, status, case_id, question_id))
        await touch(conn, case_id)
        return row


@router.post("/cases/{case_id}/defense/questions/{question_id}/simplify")
async def simplify_question(request: Request, demo_id: DemoId, case_id: UUID, question_id: UUID,
                            payload: Revision):
    db = get_db(request)
    async with db.connection() as conn:
        case = await owned_case(conn, demo_id, case_id)
        require_status(case, "defense")
        question = await question_for(conn, case_id, question_id)
        require_pending(question, payload.revision)
        if question["revision"] >= 2:
            raise APIError(409, "REVISION_LIMIT", "질문은 두 번까지 다시 표현할 수 있습니다.")
    draft = await generate(request, "simplify", prompt=question["prompt"])
    async with db.connection() as conn:
        current = await owned_case(conn, demo_id, case_id, lock=True)
        require_version(current, case["version"])
        row = await fetch_one(conn, f"""
            UPDATE court_demo.defense_turns
            SET question = %s, revision = revision + 1,
                prompts = array_append(prompts, %s), suggested_answers = %s
            WHERE case_id = %s AND id = %s RETURNING {_QUESTION_FIELDS}
        """, (draft["prompt"], draft["prompt"], Jsonb(draft["suggested_answers"]),
              case_id, question_id))
        await touch(conn, case_id)
        return row


@router.post("/cases/{case_id}/decision")
async def make_decision(request: Request, demo_id: DemoId, case_id: UUID):
    db = get_db(request)
    async with db.connection() as conn:
        case = await owned_case(conn, demo_id, case_id)
        if case["status"] in ("decision_review", "closed"):
            return {"status": case["status"], "version": case["version"],
                    "decision": await decision_for(conn, case_id)}
        require_status(case, "defense")
        turns = await turns_for(conn, case_id)
        if not turns or turns[-1]["answer_status"] == "pending":
            raise APIError(409, "DEFENSE_INCOMPLETE", "변론 질문에 답하거나 건너뛴 뒤 판결을 요청해 주세요.")
        evidence = await evidence_for(conn, case_id)
    draft = await generate(request, "decision", **context_for(case, evidence, turns))
    async with db.connection() as conn:
        current = await owned_case(conn, demo_id, case_id, lock=True)
        require_version(current, case["version"])
        await conn.execute("""
            INSERT INTO court_demo.case_decisions
                (case_id, draft_type, reported_facts, unsupported_claims, suggested_statement, next_action)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (case_id, draft["type"], draft["reported_facts"], draft["unsupported_claims"],
              draft["suggested_statement"], draft["next_action"]))
        progress = await touch(conn, case_id, "decision_review")
        return {"decision": await decision_for(conn, case_id), **progress}


@router.post("/cases/{case_id}/decision/rephrase")
async def rephrase_decision(request: Request, demo_id: DemoId, case_id: UUID, payload: Rephrase):
    db = get_db(request)
    async with db.connection() as conn:
        case = await owned_case(conn, demo_id, case_id)
        require_status(case, "decision_review")
        require_version(case, payload.version)
        decision = await decision_for(conn, case_id)
        if not decision or not decision["suggested_statement"]:
            raise APIError(409, "STATEMENT_MISSING", "다시 표현할 판결 문장이 없습니다.")
    draft = await generate(request, "rephrase", suggested_statement=decision["suggested_statement"],
                           style=payload.style)
    async with db.connection() as conn:
        current = await owned_case(conn, demo_id, case_id, lock=True)
        require_version(current, case["version"])
        await conn.execute("UPDATE court_demo.case_decisions SET suggested_statement = %s WHERE case_id = %s",
                           (draft["suggested_statement"], case_id))
        return {**draft, **(await touch(conn, case_id))}


@router.put("/cases/{case_id}/decision/final")
async def finalize(request: Request, demo_id: DemoId, case_id: UUID, payload: FinalDecision):
    async with get_db(request).connection() as conn:
        case = await owned_case(conn, demo_id, case_id, lock=True)
        require_status(case, "decision_review")
        if needs_support(payload.final_statement):
            await touch(conn, case_id, "support_required")
            return support_response()
        emotion = payload.emotion_after
        await conn.execute("""
            UPDATE court_demo.case_decisions SET final_type = %s, final_statement = %s, finalized_at = now()
            WHERE case_id = %s
        """, (payload.decision_type, payload.final_statement, case_id))
        row = await fetch_one(conn, f"""
            UPDATE court_demo.cases SET status = 'closed', closed_at = now(), updated_at = now(),
                version = version + 1, emotion_after_type = %s, emotion_after_intensity = %s
            WHERE id = %s RETURNING {_CASE_FIELDS}
        """, (emotion.type if emotion else None, emotion.intensity if emotion else None, case_id))
        return {"case": case_detail_fields(row), "decision": await decision_for(conn, case_id)}


@router.get("/cases/{case_id}/follow-up/options")
async def follow_up_options(request: Request, demo_id: DemoId, case_id: UUID):
    async with get_db(request).connection() as conn:
        case = await owned_case(conn, demo_id, case_id)
        require_status(case, "closed")
        decision = await decision_for(conn, case_id)
    action = decision["next_action"] if decision else None
    recommended = (
        {"title": "다음 행동 제안", "description": action}
        if action and len(action) <= 500 and not needs_support(action)
        else {field: _FOLLOW_UP_OPTIONS[0][field] for field in ("title", "description")}
    )
    return {"recommended": recommended, "options": _FOLLOW_UP_OPTIONS}


@router.put("/cases/{case_id}/follow-up")
async def follow_up(request: Request, demo_id: DemoId, case_id: UUID, payload: FollowUp):
    async with get_db(request).connection() as conn:
        case = await owned_case(conn, demo_id, case_id, lock=True)
        require_status(case, "closed")
        if needs_support(payload.action_text):
            return support_response()
        saved = await fetch_one(conn, """
            UPDATE court_demo.case_decisions SET follow_up_choice = %s, follow_up_action = %s,
                follow_up_updated_at = now() WHERE case_id = %s
            RETURNING follow_up_updated_at AS updated_at
        """, (payload.choice, payload.action_text, case_id))
        await touch(conn, case_id)
        value = {"choice": payload.choice, "action_text": payload.action_text}
        return {**value, "follow_up": {**value, "updated_at": saved["updated_at"]}}
