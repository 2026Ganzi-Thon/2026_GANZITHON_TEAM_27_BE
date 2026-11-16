"""DB 모델 (명세 4. PostgreSQL 및 ERD, 전시용 demo_id 버전).

SQLite(개발)와 PostgreSQL(Supabase) 양쪽에서 동작하도록 enum 은 문자열+CHECK,
배열은 JSON 으로 저장한다. Supabase 용 DDL 은 schema.sql 에 있다.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _in(col: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({joined})"


CASE_STATUSES = ("received", "evidence_review", "defense", "decision_review", "closed", "support_required")
EMOTION_TYPES = ("anxiety", "sadness", "anger", "shame", "guilt", "other")
EVIDENCE_TYPES = ("observed", "reported", "interpretation", "unknown")
ANSWER_STATUSES = ("pending", "answered", "skipped", "not_sure")
DECISION_TYPES = ("reframed", "deferred", "action_needed")
FOLLOW_UP_CHOICES = ("action", "rest")
POST_VISIBILITIES = ("public", "password_protected")
POST_STATUSES = ("pending_review", "published", "hidden")
REPORT_REASONS = ("personal_information", "harmful_content", "other")


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("demo_id", "case_number", name="uq_cases_demo_case_number"),
        CheckConstraint(_in("status", CASE_STATUSES), name="ck_cases_status"),
        CheckConstraint(
            "(emotion_before_type IS NULL) = (emotion_before_intensity IS NULL)", name="ck_cases_emotion_before_pair"
        ),
        CheckConstraint(
            "(emotion_after_type IS NULL) = (emotion_after_intensity IS NULL)", name="ck_cases_emotion_after_pair"
        ),
        Index("ix_cases_demo_created", "demo_id", "created_at", "id"),
        Index("ix_cases_demo_closed", "demo_id", "closed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    demo_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    case_number: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    original_thought: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    claim: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    pattern_candidates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    pattern_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    emotion_before_type: Mapped[str | None] = mapped_column(String(16))
    emotion_before_intensity: Mapped[int | None] = mapped_column(SmallInteger)
    emotion_after_type: Mapped[str | None] = mapped_column(String(16))
    emotion_after_intensity: Mapped[int | None] = mapped_column(SmallInteger)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    evidence: Mapped[list["CaseEvidence"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True, order_by="CaseEvidence.position"
    )
    turns: Mapped[list["DefenseTurn"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True, order_by="DefenseTurn.sequence"
    )
    decision: Mapped["CaseDecision | None"] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )
    posts: Mapped[list["CasePost"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )

    def touch(self) -> None:
        self.version = (self.version or 0) + 1
        self.updated_at = utcnow()


class CaseEvidence(Base):
    __tablename__ = "case_evidence"
    __table_args__ = (
        UniqueConstraint("case_id", "position", name="uq_case_evidence_position"),
        CheckConstraint(_in("type", EVIDENCE_TYPES), name="ck_case_evidence_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    case: Mapped[Case] = relationship(back_populates="evidence")


class DefenseTurn(Base):
    __tablename__ = "defense_turns"
    __table_args__ = (
        UniqueConstraint("case_id", "sequence", name="uq_defense_turns_sequence"),
        CheckConstraint("sequence BETWEEN 1 AND 3", name="ck_defense_turns_sequence"),
        CheckConstraint("revision BETWEEN 0 AND 2", name="ck_defense_turns_revision"),
        CheckConstraint(_in("answer_status", ANSWER_STATUSES), name="ck_defense_turns_answer_status"),
        CheckConstraint("(answer_status = 'answered') = (answer IS NOT NULL)", name="ck_defense_turns_answer_text"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)  # 최신 표현
    prompts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # 표현 기록 (처음 → 최신)
    suggested_answers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    answer: Mapped[str | None] = mapped_column(Text)
    answer_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    revision: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped[Case] = relationship(back_populates="turns")


class CaseDecision(Base):
    __tablename__ = "case_decisions"
    __table_args__ = (
        CheckConstraint(_in("draft_type", DECISION_TYPES), name="ck_case_decisions_draft_type"),
        CheckConstraint(f"final_type IS NULL OR {_in('final_type', DECISION_TYPES)}", name="ck_case_decisions_final_type"),
        CheckConstraint(
            f"follow_up_choice IS NULL OR {_in('follow_up_choice', FOLLOW_UP_CHOICES)}",
            name="ck_case_decisions_follow_up_choice",
        ),
        CheckConstraint(
            "follow_up_choice IS NULL OR follow_up_choice <> 'action' OR follow_up_action IS NOT NULL",
            name="ck_case_decisions_follow_up_action",
        ),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), primary_key=True)
    draft_type: Mapped[str] = mapped_column(String(16), nullable=False)
    reported_facts: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    unsupported_claims: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    suggested_statement: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text)
    final_type: Mapped[str | None] = mapped_column(String(16))
    final_statement: Mapped[str | None] = mapped_column(Text)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    follow_up_choice: Mapped[str | None] = mapped_column(String(16))
    follow_up_action: Mapped[str | None] = mapped_column(Text)
    follow_up_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="decision")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    demo_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    response_status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CasePost(Base):
    __tablename__ = "case_posts"
    __table_args__ = (
        CheckConstraint(_in("visibility", POST_VISIBILITIES), name="ck_case_posts_visibility"),
        CheckConstraint(_in("status", POST_STATUSES), name="ck_case_posts_status"),
        CheckConstraint(
            "(visibility = 'password_protected') = (password_hash IS NOT NULL)", name="ck_case_posts_password_hash"
        ),
        Index("ix_case_posts_case", "case_id"),
        Index("ix_case_posts_demo_created", "demo_id", "created_at", "id"),
        Index("ix_case_posts_feed", "published_at", "id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    demo_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_review")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    pattern_tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    password_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped[Case | None] = relationship(back_populates="posts")
    reports: Mapped[list["PostReport"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    likes: Mapped[list["PostLike"]] = relationship(cascade="all, delete-orphan", passive_deletes=True)
    comments: Mapped[list["PostComment"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, order_by="PostComment.created_at"
    )


class PostReport(Base):
    __tablename__ = "post_reports"
    __table_args__ = (CheckConstraint(_in("reason", REPORT_REASONS), name="ck_post_reports_reason"),)

    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_posts.id", ondelete="CASCADE"), primary_key=True)
    reporter_demo_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PostLike(Base):
    """(확장) 게시글 공감. 프론트 게시판 상세 화면용."""

    __tablename__ = "post_likes"

    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_posts.id", ondelete="CASCADE"), primary_key=True)
    demo_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class PostComment(Base):
    """(확장) 게시글 댓글. 프론트 게시판 상세 화면용."""

    __tablename__ = "post_comments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_posts.id", ondelete="CASCADE"), nullable=False)
    demo_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
