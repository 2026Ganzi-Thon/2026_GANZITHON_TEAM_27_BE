"""응답 직렬화."""

import uuid
from datetime import datetime, timezone

from app.models import Case, CaseDecision, CasePost, DefenseTurn

MAX_QUESTIONS = 3
MAX_SIMPLIFY = 2


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:  # SQLite 는 tz 정보 없이 돌려준다 (UTC 로 저장)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def emotion(type_: str | None, intensity: int | None) -> dict | None:
    if type_ is None or intensity is None:
        return None
    return {"type": type_, "intensity": intensity}


def suggested(answers: list) -> list[dict]:
    return [{"rank": i + 1, "text": t} for i, t in enumerate(answers or [])]


def question(turn: DefenseTurn) -> dict:
    prompts = list(turn.prompts or []) or [turn.question]
    return {
        "id": str(turn.id),
        "sequence": turn.sequence,
        "prompt": turn.question,
        "revision": turn.revision,
        "prompts": prompts,
        "suggested_answers": suggested(turn.suggested_answers),
        "answer_status": turn.answer_status,
        "answer": turn.answer,
        "answered_at": iso(turn.answered_at),
        "can_simplify": turn.answer_status == "pending" and turn.revision < MAX_SIMPLIFY,
    }


def decision_draft(d: CaseDecision, version: int) -> dict:
    return {
        "type": d.draft_type,
        "reported_facts": list(d.reported_facts or []),
        "unsupported_claims": list(d.unsupported_claims or []),
        "suggested_statement": d.suggested_statement,
        "next_action": d.next_action,
        "version": version,
    }


def decision_full(d: CaseDecision, version: int) -> dict:
    out = decision_draft(d, version)
    out.update(
        {
            "type": d.final_type or d.draft_type,
            "draft_type": d.draft_type,
            "final_type": d.final_type,
            "final_statement": d.final_statement,
            "finalized_at": iso(d.finalized_at),
        }
    )
    return out


def follow_up(d: CaseDecision | None) -> dict | None:
    if d is None or d.follow_up_choice is None:
        return None
    return {
        "choice": d.follow_up_choice,
        "action_text": d.follow_up_action,
        "updated_at": iso(d.follow_up_updated_at),
    }


def case_brief(c: Case) -> dict:
    """접수 응답·목록 항목."""
    d = c.decision
    return {
        "id": str(c.id),
        "case_number": c.case_number,
        "status": c.status,
        "title": c.title,
        "thought": c.original_thought,
        "summary": c.summary,
        "pattern_tags": list(c.pattern_tags or []),
        "emotion": emotion(c.emotion_before_type, c.emotion_before_intensity),
        "emotion_after": emotion(c.emotion_after_type, c.emotion_after_intensity),
        "final_statement": d.final_statement if d else None,
        "created_at": iso(c.created_at),
        "closed_at": iso(c.closed_at),
    }


def case_detail(c: Case) -> dict:
    turns = [question(t) for t in c.turns]
    open_q = next((q for q in turns if q["answer_status"] == "pending"), None)
    processed = sum(1 for q in turns if q["answer_status"] != "pending")
    d = c.decision
    return {
        "id": str(c.id),
        "case_number": c.case_number,
        "status": c.status,
        "version": c.version,
        "title": c.title,
        "thought": c.original_thought,
        "claim": c.claim,
        "summary": c.summary,
        "pattern_candidates": list(c.pattern_candidates or []),
        "pattern_tags": list(c.pattern_tags or []),
        "evidence": [{"type": e.type, "text": e.text} for e in c.evidence],
        "emotion": emotion(c.emotion_before_type, c.emotion_before_intensity),
        "emotion_after": emotion(c.emotion_after_type, c.emotion_after_intensity),
        "defense": turns,
        "open_question": open_q,
        "defense_summary": {
            "asked": len(turns),
            "processed": processed,
            "remaining": MAX_QUESTIONS - len(turns),
            "can_request_decision": c.status == "defense" and processed >= 1 and open_q is None,
        },
        "decision": decision_full(d, c.version) if d else None,
        "final_statement": d.final_statement if d else None,
        "follow_up": follow_up(d),
        "created_at": iso(c.created_at),
        "updated_at": iso(c.updated_at),
        "closed_at": iso(c.closed_at),
    }


def prosecution_view(c: Case) -> dict:
    return {
        "status": c.status,
        "title": c.title,
        "claim": c.claim,
        "summary": c.summary,
        "pattern_candidates": list(c.pattern_candidates or []),
        "pattern_tags": list(c.pattern_tags or []),
        "evidence": [{"type": e.type, "text": e.text} for e in c.evidence],
        "version": c.version,
    }


def post_item(p: CasePost, viewer: uuid.UUID, like_count: int = 0, comment_count: int = 0) -> dict:
    mine = p.demo_id == viewer
    locked = p.visibility == "password_protected" and not mine
    return {
        "id": str(p.id),
        "title": None if locked else p.title,
        "body": None if locked else p.body,
        "tags": list(p.tags or []),
        "pattern_tags": list(p.pattern_tags or []),
        "visibility": p.visibility,
        "status": p.status,
        "is_mine": mine,
        "is_hot": False,
        "like_count": like_count,
        "comment_count": comment_count,
        "created_at": iso(p.created_at),
        "published_at": iso(p.published_at),
    }


def post_detail(p: CasePost, viewer: uuid.UUID) -> dict:
    likes = list(p.likes)
    comments = list(p.comments)
    return {
        "id": str(p.id),
        "title": p.title,
        "body": p.body,
        "content": p.body,
        "tags": list(p.tags or []),
        "pattern_tags": list(p.pattern_tags or []),
        "visibility": p.visibility,
        "status": p.status,
        "author": "익명",
        "is_mine": p.demo_id == viewer,
        "like_count": len(likes),
        "is_liked": any(lk.demo_id == viewer for lk in likes),
        "comment_count": len(comments),
        "comments": [
            {
                "id": str(cm.id),
                "author": "익명",
                "content": cm.content,
                "created_at": iso(cm.created_at),
                "is_mine": cm.demo_id == viewer,
            }
            for cm in comments
        ],
        "created_at": iso(p.created_at),
        "published_at": iso(p.published_at),
    }
