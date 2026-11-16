"""요청 본문 검증 (명세 3.1 입력 규칙, 앞뒤 공백 제거 후 기준)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

EmotionType = Literal["anxiety", "sadness", "anger", "shame", "guilt", "other"]
Pattern = Literal[
    "all_or_nothing", "overgeneralization", "mind_reading", "catastrophizing", "labeling", "discounting_positives"
]
EvidenceType = Literal["observed", "reported", "interpretation", "unknown"]
DecisionType = Literal["reframed", "deferred", "action_needed"]
CaseStatus = Literal["received", "evidence_review", "defense", "decision_review", "closed", "support_required"]

Text2000 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Text1000 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Text500 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Text120 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=24)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Emotion(Strict):
    type: EmotionType
    intensity: int = Field(ge=0, le=100, strict=True)


class CreateCaseIn(Strict):
    thought: Text2000
    emotion: Emotion | None = None


class EvidenceItem(Strict):
    type: EvidenceType
    text: Text2000


class EvidenceIn(Strict):
    claim: Text2000
    pattern_tags: list[Pattern] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list, max_length=20)
    title: Text120 | None = None

    @field_validator("pattern_tags")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


class AnswerIn(Strict):
    action: Literal["answer", "skip", "not_sure"]
    text: str | None = None
    revision: int = Field(ge=0)

    @model_validator(mode="after")
    def _text_rule(self):
        if self.action == "answer":
            text = (self.text or "").strip()
            if not 1 <= len(text) <= 1000:
                raise ValueError("answer 는 text 가 1~1,000자여야 합니다.")
            self.text = text
        else:
            self.text = None
        return self


class SimplifyIn(Strict):
    revision: int = Field(ge=0)


class RephraseIn(Strict):
    style: Literal["neutral", "shorter", "gentler"] = "neutral"
    version: int = Field(ge=0)


class FinalIn(Strict):
    decision_type: DecisionType
    final_statement: str | None = None
    emotion_after: Emotion | None = None
    version: int | None = None

    @model_validator(mode="after")
    def _statement_rule(self):
        text = (self.final_statement or "").strip()
        if not text:
            if self.decision_type != "deferred":
                raise ValueError("final_statement 는 deferred 일 때만 생략할 수 있습니다.")
            self.final_statement = None
        elif len(text) > 1000:
            raise ValueError("final_statement 는 1~1,000자여야 합니다.")
        else:
            self.final_statement = text
        return self


class FollowUpIn(Strict):
    choice: Literal["action", "rest"]
    action_text: str | None = None

    @model_validator(mode="after")
    def _action_rule(self):
        if self.choice == "action":
            text = (self.action_text or "").strip()
            if not 1 <= len(text) <= 500:
                raise ValueError("choice=action 이면 action_text 가 1~500자여야 합니다.")
            self.action_text = text
        else:
            self.action_text = None
        return self


class PostIn(Strict):
    visibility: Literal["public", "password_protected"]
    title: Text120
    body: Text2000
    tags: list[Tag] = Field(default_factory=list, max_length=5)
    password: str | None = None

    @model_validator(mode="after")
    def _password_rule(self):
        if self.visibility == "password_protected":
            # 명세는 8~64자, 프론트 화면 규칙(6자 이상)에 맞춰 6~64자를 받는다.
            if self.password is None or not 6 <= len(self.password) <= 64:
                raise ValueError("password_protected 글은 비밀번호가 6~64자여야 합니다.")
        else:
            self.password = None
        self.tags = list(dict.fromkeys(self.tags))
        return self


class UnlockIn(Strict):
    password: str = Field(min_length=1, max_length=64)


class ReportIn(Strict):
    reason: Literal["personal_information", "harmful_content", "other"]


class CommentIn(Strict):
    content: Text500
