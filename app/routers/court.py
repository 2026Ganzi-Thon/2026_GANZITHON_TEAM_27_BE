"""법정 진행: 기소·증거 확정·변론·판결·작은 행동.

AI 호출은 DB 트랜잭션 밖에서 수행하고, 저장 직전에 사건의 상태와 version 을 다시 확인한다.
"""

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import serializers as ser
from app.ai import graphs
from app.db import get_db
from app.deps import demo_id_dep
from app.errors import conflict, invalid_state, not_found
from app.models import Case, CaseDecision, CaseEvidence, DefenseTurn, utcnow
from app.routers.cases import load_case
from app.safety import needs_support, support_payload
from app.schemas import AnswerIn, EvidenceIn, FinalIn, FollowUpIn, RephraseIn, SimplifyIn
from app.utils import parse_uuid

router = APIRouter(prefix="/cases/{case_id}", tags=["court"])

STATE_MESSAGES = {
    "received": "아직 기소 초안을 만들지 않은 사건이에요.",
    "evidence_review": "근거를 확인한 뒤 변론 질문을 요청할 수 있습니다.",
    "defense": "변론을 마친 뒤 판결을 요청할 수 있어요.",
    "decision_review": "판결 초안을 검토하는 단계예요.",
    "closed": "이미 종결된 사건이에요.",
    "support_required": "지원 안내로 전환된 사건이라 법정 진행을 이어갈 수 없어요.",
}


def _state_error(case: Case, expected: str):
    return invalid_state(f"{STATE_MESSAGES.get(case.status, '현재 단계에서 할 수 없는 요청이에요.')} (현재: {case.status}, 필요: {expected})")


def _end_txn(db: Session) -> None:
    """AI 호출 전에 읽기 트랜잭션을 끝낸다."""
    db.commit()


def _evidence_dicts(case: Case) -> list[dict]:
    return [{"type": e.type, "text": e.text} for e in case.evidence]


def _turn_dicts(case: Case) -> list[dict]:
    return [
        {"sequence": t.sequence, "prompt": t.question, "answer_status": t.answer_status, "answer": t.answer}
        for t in case.turns
    ]


def _emotion(case: Case) -> dict | None:
    return ser.emotion(case.emotion_before_type, case.emotion_before_intensity)


# ───────────────────────── 기소 ─────────────────────────


@router.post("/prosecution")
def prosecution(case_id: str, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)):
    case = load_case(db, demo_id, case_id)
    if case.status == "support_required":
        raise _state_error(case, "received")
    if case.status != "received":
        return ser.prosecution_view(case)  # 재시도·뒤로 가기: 저장된 초안

    version, thought, emotion = case.version, case.original_thought, _emotion(case)
    _end_txn(db)
    draft = graphs.generate_prosecution(thought, emotion)

    case = load_case(db, demo_id, case_id, for_update=True)
    if case.version != version or case.status != "received":
        db.rollback()
        if case.status == "support_required":
            raise _state_error(case, "received")
        return ser.prosecution_view(case)  # 동시에 들어온 요청이 먼저 저장함

    case.title = draft.title
    case.claim = draft.claim
    case.summary = draft.summary
    case.pattern_candidates = list(draft.pattern_candidates)
    case.pattern_tags = list(draft.pattern_candidates)
    case.evidence = [CaseEvidence(type=e.type, text=e.text, position=i) for i, e in enumerate(draft.evidence)]
    case.status = "evidence_review"
    case.touch()
    db.commit()
    db.refresh(case)
    return ser.prosecution_view(case)


@router.put("/evidence")
def confirm_evidence(
    case_id: str, body: EvidenceIn, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)
):
    case = load_case(db, demo_id, case_id, for_update=True)
    if case.status != "evidence_review":
        raise _state_error(case, "evidence_review")
    case.claim = body.claim
    if body.title:
        case.title = body.title
    case.pattern_tags = list(body.pattern_tags)
    case.evidence.clear()
    db.flush()
    case.evidence = [CaseEvidence(type=e.type, text=e.text, position=i) for i, e in enumerate(body.evidence)]
    case.status = "defense"
    case.touch()
    db.commit()
    db.refresh(case)
    return ser.case_detail(case)


# ───────────────────────── 변론 ─────────────────────────


def _find_turn(case: Case, question_id: str) -> DefenseTurn:
    qid = parse_uuid(question_id, "질문을 찾을 수 없어요.")
    turn = next((t for t in case.turns if t.id == qid), None)
    if turn is None:
        raise not_found("질문을 찾을 수 없어요.")
    return turn


def _question_out(case: Case, turn: DefenseTurn) -> dict:
    out = ser.question(turn)
    out["remaining"] = ser.MAX_QUESTIONS - len(case.turns)
    return out


@router.post("/defense/questions")
def next_question(case_id: str, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)):
    case = load_case(db, demo_id, case_id)
    if case.status != "defense":
        raise _state_error(case, "defense")
    pending = next((t for t in case.turns if t.answer_status == "pending"), None)
    if pending:
        return JSONResponse(status_code=200, content=_question_out(case, pending))
    if len(case.turns) >= ser.MAX_QUESTIONS:
        raise conflict("DEFENSE_COMPLETED", "질문 3개를 모두 처리했어요. 판결을 받을 수 있어요.")

    version = case.version
    args = (case.claim or case.original_thought, list(case.pattern_tags or []), _evidence_dicts(case), _turn_dicts(case))
    _end_txn(db)
    generated = graphs.generate_question(*args)

    case = load_case(db, demo_id, case_id, for_update=True)
    if case.version != version:
        db.rollback()
        pending = next((t for t in case.turns if t.answer_status == "pending"), None)
        if case.status == "defense" and pending:
            return JSONResponse(status_code=200, content=_question_out(case, pending))
        raise conflict("VERSION_CONFLICT", "사건이 그사이 바뀌었어요. 다시 불러와 주세요.")

    turn = DefenseTurn(
        sequence=len(case.turns) + 1,
        question=generated.prompt,
        prompts=[generated.prompt],
        suggested_answers=list(generated.suggested_answers),
        answer_status="pending",
        revision=0,
    )
    case.turns.append(turn)
    case.touch()
    db.commit()
    db.refresh(case)
    return JSONResponse(status_code=201, content=_question_out(case, turn))


@router.put("/defense/questions/{question_id}/answer")
def answer_question(
    case_id: str,
    question_id: str,
    body: AnswerIn,
    db: Session = Depends(get_db),
    demo_id: uuid.UUID = Depends(demo_id_dep),
):
    case = load_case(db, demo_id, case_id, for_update=True)
    if case.status != "defense":
        raise _state_error(case, "defense")
    turn = _find_turn(case, question_id)
    if turn.answer_status != "pending":
        raise invalid_state("이미 처리한 질문이에요.")
    if body.revision != turn.revision:
        raise conflict("STALE_REVISION", "질문이 바뀌었어요. 최신 질문으로 다시 답해 주세요.")

    if body.action == "answer" and needs_support(body.text):
        # 원문은 저장하지 않고 사건을 지원 안내 상태로 전환한다.
        case.status = "support_required"
        case.touch()
        db.commit()
        return support_payload()

    turn.answer_status = {"answer": "answered", "skip": "skipped", "not_sure": "not_sure"}[body.action]
    turn.answer = body.text if body.action == "answer" else None
    turn.answered_at = utcnow()
    case.touch()
    db.commit()
    db.refresh(case)

    processed = sum(1 for t in case.turns if t.answer_status != "pending")
    return {
        "outcome": "answered",
        "question": ser.question(turn),
        "processed_count": processed,
        "remaining": ser.MAX_QUESTIONS - len(case.turns),
        "can_request_decision": processed >= 1,
        "version": case.version,
    }


@router.post("/defense/questions/{question_id}/simplify")
def simplify_question(
    case_id: str,
    question_id: str,
    body: SimplifyIn,
    db: Session = Depends(get_db),
    demo_id: uuid.UUID = Depends(demo_id_dep),
):
    case = load_case(db, demo_id, case_id)
    if case.status != "defense":
        raise _state_error(case, "defense")
    turn = _find_turn(case, question_id)
    if turn.answer_status != "pending":
        raise invalid_state("답하지 않은 질문만 쉽게 바꿀 수 있어요.")
    if body.revision != turn.revision:
        raise conflict("STALE_REVISION", "질문이 바뀌었어요. 다시 불러와 주세요.")
    if turn.revision >= ser.MAX_SIMPLIFY:
        raise conflict("SIMPLIFY_LIMIT", "질문은 두 번까지만 쉽게 바꿀 수 있어요.")

    prompt, history, revision, tid = turn.question, list(turn.prompts or [turn.question]), turn.revision, turn.id
    _end_txn(db)
    simplified = graphs.generate_simplified(prompt, history, revision)

    case = load_case(db, demo_id, case_id, for_update=True)
    turn = next((t for t in case.turns if t.id == tid), None)
    if turn is None or turn.revision != revision or turn.answer_status != "pending":
        db.rollback()
        raise conflict("STALE_REVISION", "질문이 바뀌었어요. 다시 불러와 주세요.")
    turn.question = simplified.prompt
    turn.prompts = history + [simplified.prompt]
    turn.revision = revision + 1
    case.touch()
    db.commit()
    return {"id": str(turn.id), "sequence": turn.sequence, "prompt": turn.question, "revision": turn.revision}


# ───────────────────────── 판결 ─────────────────────────


def _decision_response(case: Case) -> dict:
    return {"status": case.status, "decision": ser.decision_full(case.decision, case.version)}


@router.post("/decision")
def request_decision(case_id: str, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)):
    case = load_case(db, demo_id, case_id)
    if case.status in ("decision_review", "closed") and case.decision:
        return _decision_response(case)  # 재시도: 저장된 초안
    if case.status != "defense":
        raise _state_error(case, "defense")
    processed = sum(1 for t in case.turns if t.answer_status != "pending")
    if processed < 1 or any(t.answer_status == "pending" for t in case.turns):
        raise invalid_state("변론 질문을 하나 이상 처리하고, 답하지 않은 질문이 없어야 판결을 받을 수 있어요.")

    version = case.version
    args = (
        case.original_thought,
        case.claim or case.original_thought,
        list(case.pattern_tags or []),
        _evidence_dicts(case),
        _turn_dicts(case),
    )
    _end_txn(db)
    draft = graphs.generate_decision(*args)

    case = load_case(db, demo_id, case_id, for_update=True)
    if case.version != version:
        db.rollback()
        if case.decision and case.status in ("decision_review", "closed"):
            return _decision_response(case)
        raise conflict("VERSION_CONFLICT", "사건이 그사이 바뀌었어요. 다시 불러와 주세요.")

    case.decision = CaseDecision(
        draft_type=draft.type,
        reported_facts=list(draft.reported_facts),
        unsupported_claims=list(draft.unsupported_claims),
        suggested_statement=draft.suggested_statement,
        next_action=draft.next_action,
    )
    case.status = "decision_review"
    case.touch()
    db.commit()
    db.refresh(case)
    return _decision_response(case)


@router.post("/decision/rephrase")
def rephrase_decision(
    case_id: str, body: RephraseIn, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)
):
    case = load_case(db, demo_id, case_id)
    if case.status != "decision_review" or not case.decision:
        raise _state_error(case, "decision_review")
    if body.version != case.version:
        raise conflict("VERSION_CONFLICT", "판결 내용이 바뀌었어요. 다시 불러와 주세요.")

    version, statement, dtype = case.version, case.decision.suggested_statement, case.decision.draft_type
    _end_txn(db)
    out = graphs.generate_rephrase(statement, body.style, dtype)

    case = load_case(db, demo_id, case_id, for_update=True)
    if case.version != version or case.status != "decision_review":
        db.rollback()
        raise conflict("VERSION_CONFLICT", "판결 내용이 바뀌었어요. 다시 불러와 주세요.")
    case.decision.suggested_statement = out.suggested_statement
    case.decision.updated_at = utcnow()
    case.touch()
    db.commit()
    return {"suggested_statement": out.suggested_statement, "version": case.version}


@router.put("/decision/final")
def finalize_decision(
    case_id: str, body: FinalIn, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)
):
    case = load_case(db, demo_id, case_id, for_update=True)
    if case.status != "decision_review" or not case.decision:
        raise _state_error(case, "decision_review")
    if body.version is not None and body.version != case.version:
        raise conflict("VERSION_CONFLICT", "판결 내용이 바뀌었어요. 다시 불러와 주세요.")

    if needs_support(body.final_statement):
        case.status = "support_required"
        case.touch()
        db.commit()
        return support_payload()

    now = utcnow()
    d = case.decision
    d.final_type = body.decision_type
    d.final_statement = body.final_statement
    d.finalized_at = now
    d.updated_at = now
    if body.emotion_after:
        case.emotion_after_type = body.emotion_after.type
        case.emotion_after_intensity = body.emotion_after.intensity
    case.status = "closed"
    case.closed_at = now
    case.touch()
    db.commit()
    db.refresh(case)
    return ser.case_detail(case)


@router.put("/follow-up")
def save_follow_up(
    case_id: str, body: FollowUpIn, db: Session = Depends(get_db), demo_id: uuid.UUID = Depends(demo_id_dep)
):
    case = load_case(db, demo_id, case_id, for_update=True)
    if case.status != "closed" or not case.decision:
        raise _state_error(case, "closed")
    if needs_support(body.action_text):
        db.rollback()
        return support_payload()  # 종결 기록은 그대로 두고 이 요청만 중단
    d = case.decision
    d.follow_up_choice = body.choice
    d.follow_up_action = body.action_text
    d.follow_up_updated_at = utcnow()
    case.touch()
    db.commit()
    return {"follow_up": ser.follow_up(d)}
