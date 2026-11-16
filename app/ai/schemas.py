"""AI 응답 형식 (Pydantic 으로 검증한다)."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

PatternCode = Literal[
    "all_or_nothing", "overgeneralization", "mind_reading", "catastrophizing", "labeling", "discounting_positives"
]


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class EvidenceDraft(BaseModel):
    type: Literal["observed", "reported", "interpretation", "unknown"]
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _t(cls, v: str) -> str:
        return _clip(v, 200)


class ProsecutionDraft(BaseModel):
    """검사 AI: 사건명·주장·사고 패턴 후보·근거 초안."""

    title: str = Field(min_length=1, description="'○○ 사건' 형식, 15자 안팎의 사건명")
    claim: str = Field(min_length=1, description="사용자를 괴롭히는 생각을 한 문장으로 (40자 안팎)")
    summary: str = Field(min_length=1, description="접수 글 한 줄 요약 (40자 이내)")
    pattern_candidates: list[PatternCode] = Field(default_factory=list, description="사고 패턴 후보 0~2개")
    evidence: list[EvidenceDraft] = Field(min_length=1, description="사용자 글에서 뽑은 근거 1~4개")

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return _clip(v, 30)

    @field_validator("claim")
    @classmethod
    def _claim(cls, v: str) -> str:
        return _clip(v.strip().strip('"“”'), 120)

    @field_validator("summary")
    @classmethod
    def _summary(cls, v: str) -> str:
        return _clip(v, 60)

    @field_validator("pattern_candidates")
    @classmethod
    def _patterns(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))[:2]

    @field_validator("evidence")
    @classmethod
    def _evidence(cls, v: list[EvidenceDraft]) -> list[EvidenceDraft]:
        return v[:4]


class DefenseQuestion(BaseModel):
    """변호인 AI: 다음 질문 하나와 추천 답변 3개."""

    prompt: str = Field(min_length=1, description="한 문장 질문 (1~200자)")
    suggested_answers: list[str] = Field(default_factory=list, description="사용자가 고를 수 있는 짧은 답변 3개")

    @field_validator("prompt")
    @classmethod
    def _p(cls, v: str) -> str:
        return _clip(v, 300)

    @field_validator("suggested_answers")
    @classmethod
    def _s(cls, v: list[str]) -> list[str]:
        return [_clip(x, 80) for x in v if str(x).strip()][:3]


class SimplifiedQuestion(BaseModel):
    prompt: str = Field(min_length=1, description="같은 뜻을 더 쉽고 짧게 바꾼 질문")

    @field_validator("prompt")
    @classmethod
    def _p(cls, v: str) -> str:
        return _clip(v, 300)


class DecisionDraft(BaseModel):
    """판사 AI: 판결 초안."""

    type: Literal["reframed", "deferred", "action_needed"]
    reported_facts: list[str] = Field(default_factory=list, description="사용자가 보고한 사실")
    unsupported_claims: list[str] = Field(default_factory=list, description="근거가 부족한 해석")
    suggested_statement: str = Field(min_length=1, description="사용자가 받아들일 수 있는 수정된 생각 한두 문장")
    next_action: str | None = Field(default=None, description="action_needed 일 때 작은 다음 행동, 아니면 null")

    @field_validator("reported_facts", "unsupported_claims")
    @classmethod
    def _lists(cls, v: list[str]) -> list[str]:
        return [_clip(x, 200) for x in v if str(x).strip()][:5]

    @field_validator("suggested_statement")
    @classmethod
    def _stmt(cls, v: str) -> str:
        return _clip(v, 1000)

    @field_validator("next_action")
    @classmethod
    def _next(cls, v: str | None) -> str | None:
        if v is None or not str(v).strip() or str(v).strip().lower() in ("null", "none"):
            return None
        return _clip(v, 300)


class RephrasedStatement(BaseModel):
    suggested_statement: str = Field(min_length=1)

    @field_validator("suggested_statement")
    @classmethod
    def _stmt(cls, v: str) -> str:
        return _clip(v, 1000)
