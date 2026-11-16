"""AI 키가 없을 때 쓰는 규칙 기반 생성기.

LLM 공급자를 설정하지 않아도 전체 법정 흐름을 시연할 수 있도록 같은 형식의 결과를 만든다.
사용자가 쓰지 않은 사실은 만들지 않고, 입력 문장을 나누고 분류만 한다.
"""

import re

from app.ai.schemas import (
    DecisionDraft,
    DefenseQuestion,
    EvidenceDraft,
    ProsecutionDraft,
    RephrasedStatement,
    SimplifiedQuestion,
)

_SENT_SPLIT = re.compile(r"(?<=[.!?。…~])\s+|\n+|(?<=다\.)|(?<=요\.)")

_SITUATIONS = [
    ("발표", "발표"), ("회의", "회의"), ("면접", "면접"), ("시험", "시험"), ("과제", "과제"),
    ("프로젝트", "프로젝트"), ("상사", "상사"), ("팀장", "팀장"), ("회사", "회사"), ("직장", "직장"),
    ("학교", "학교"), ("수업", "수업"), ("친구", "친구"), ("연애", "연애"), ("남자친구", "연애"),
    ("여자친구", "연애"), ("애인", "연애"), ("가족", "가족"), ("엄마", "가족"), ("아빠", "가족"),
    ("부모", "가족"), ("단톡", "단톡방"), ("카톡", "메시지"), ("메시지", "메시지"), ("답장", "답장"),
    ("연락", "연락"), ("실수", "실수"), ("취업", "취업"), ("알바", "아르바이트"), ("운동", "운동"),
    ("다이어트", "다이어트"), ("돈", "돈"), ("성적", "성적"), ("동료", "동료"), ("선배", "선배"),
    ("후배", "후배"), ("모임", "모임"), ("SNS", "SNS"), ("인스타", "SNS"),
]

_PATTERN_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("mind_reading", ("다들", "모두", "사람들", "나를 싫어", "한심하게", "이상하게 봤", "비웃", "생각할 거", "봤을 거", "속으로")),
    ("overgeneralization", ("항상", "매번", "늘 ", "맨날", "언제나", "또 ", "역시 나는", "평생")),
    ("catastrophizing", ("끝났", "망했", "최악", "큰일", "돌이킬 수", "인생이", "다 끝", "망칠")),
    ("labeling", ("쓸모없", "한심", "바보", "멍청", "실패자", "못난", "루저", "무능", "패배자", "최악의 사람")),
    ("all_or_nothing", ("완벽", "아니면", "전부", "하나도", "절대", "무조건", "완전히", "다 망")),
    ("discounting_positives", ("운이 좋았", "별거 아니", "누구나 할", "어쩌다", "그건 그냥", "칭찬은 예의")),
]

_INTERPRETATION = ("것 같", "거다", "거야", "거예요", "겠지", "겠다", "봤을", "분명", "아마", "듯", "틀림없", "생각할", "싫어할", "한심", "쓸모없", "나는 왜")
_REPORTED = ("들었", "라고 했", "그러더", "말했", "라더라", "래요", "했대", "한대")
_UNKNOWN = ("모르겠", "모른다", "확실하지", "알 수 없", "확인 못", "확인하지")


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _sentences(text: str) -> list[str]:
    parts = [p.strip(" .,!?\t") for p in _SENT_SPLIT.split(text) if p and p.strip(" .,!?\t")]
    return parts or [text.strip()]


def _classify(sentence: str) -> str:
    if any(k in sentence for k in _UNKNOWN):
        return "unknown"
    if any(k in sentence for k in _REPORTED):
        return "reported"
    if any(k in sentence for k in _INTERPRETATION):
        return "interpretation"
    return "observed"


def _patterns(text: str) -> list[str]:
    found = [code for code, keys in _PATTERN_RULES if any(k in text for k in keys)]
    return found[:2]


def _title(text: str) -> str:
    for key, label in _SITUATIONS:
        if key in text:
            others = [lab for k, lab in _SITUATIONS if k in text and lab != label]
            if others and len(label) + len(others[0]) <= 8:
                return f"{others[0]} {label} 사건" if text.find(others[0]) < text.find(key) else f"{label} {others[0]} 사건"
            return f"{label} 사건"
    first = re.sub(r"[^가-힣A-Za-z0-9 ]", "", text).split()
    word = first[0] if first else "생각"
    word = re.sub(r"(에서|에게|한테|으로|로|은|는|이|가|을|를|에|도|만)$", "", word) or word
    return f"{word[:6]} 사건"


def _sentence_end(text: str) -> str:
    text = text.rstrip(" .")
    return text + "." if not text.endswith(("다", "요", "까", "?", "!")) else text + ("" if text.endswith(("?", "!")) else ".")


def prosecution(thought: str) -> ProsecutionDraft:
    sents = _sentences(thought)
    classified = [(s, _classify(s)) for s in sents]
    claim_candidates = [s for s, t in classified if t == "interpretation"]
    claim = claim_candidates[-1] if claim_candidates else sents[-1]
    evidence = [EvidenceDraft(type=t, text=_clip(_sentence_end(s), 60)) for s, t in classified[:4]]
    return ProsecutionDraft(
        title=_title(thought),
        claim=_clip(claim, 60),
        summary=_clip(sents[0] if len(sents[0]) >= 6 else thought, 40),
        pattern_candidates=_patterns(thought),
        evidence=evidence,
    )


_QUESTION_BANK = [
    ("{fact} 그때 실제로 확인한 반응이나 들은 말이 있나요?",
     ["부정적인 말은 듣지 못했어요.", "아직 확인하지 못했어요.", "한 사람이 어색해 보였어요."]),
    ("\"{claim}\"라는 생각과 맞지 않는 경험이 있었나요?",
     ["비슷한 상황에서 괜찮았던 적이 있어요.", "칭찬을 들은 적도 있어요.", "잘 떠오르지 않아요."]),
    ("친한 친구가 같은 일을 겪었다면 어떤 말을 해 주고 싶나요?",
     ["한 번의 일로 너를 판단할 수 없다고요.", "다음엔 더 잘할 수 있다고요.", "그럴 수 있다고 말해 줄 거예요."]),
]

_OTHER_VIEW = ("다른 사람이 같은 장면을 봤다면 어떻게 설명할 수 있을까요?",
               ["그냥 긴장했나 보다 했을 것 같아요.", "별로 신경 쓰지 않았을 것 같아요.", "잘 모르겠어요."])


def defense_question(claim: str, evidence: list[dict], previous: list[dict]) -> DefenseQuestion:
    seq = len(previous) + 1
    fact = next((e["text"] for e in evidence if e["type"] in ("observed", "reported")), "")
    fact = (fact.rstrip(".") + ".") if fact else ""
    last = previous[-1] if previous else None
    if last and last.get("answer_status") in ("not_sure", "skipped") and seq <= 3:
        prompt, answers = _OTHER_VIEW
    else:
        prompt, answers = _QUESTION_BANK[min(seq, 3) - 1]
    asked = {p.get("prompt") for p in previous}
    if prompt in asked:
        prompt, answers = _QUESTION_BANK[(seq) % 3]
    return DefenseQuestion(
        prompt=prompt.format(fact=fact, claim=_clip(claim, 40)).strip(),
        suggested_answers=answers,
    )


_SIMPLE = [
    "실제로 보거나 들은 것이 있었나요?",
    "그 생각이 맞다는 증거가 있나요?",
    "친구라면 뭐라고 말해 줄까요?",
]


def simplify(prompt: str, revision: int) -> SimplifiedQuestion:
    for i, simple in enumerate(_SIMPLE):
        if i == revision % len(_SIMPLE) and simple != prompt:
            return SimplifiedQuestion(prompt=simple)
    return SimplifiedQuestion(prompt="쉽게 말하면, " + _clip(prompt, 80))


_ACTION_KEYS = ("해야", "마감", "사과", "병원", "제출", "연락해야", "갚아야", "준비해야")


def decision(claim: str, evidence: list[dict], turns: list[dict]) -> DecisionDraft:
    facts = [e["text"] for e in evidence if e["type"] in ("observed", "reported")]
    unsupported = [e["text"] for e in evidence if e["type"] in ("interpretation", "unknown")]
    answered = [t for t in turns if t.get("answer_status") == "answered" and t.get("answer")]
    facts += [f"변론에서: {_clip(t['answer'], 60)}" for t in answered[:2]]
    if not unsupported:
        unsupported = [f"\"{_clip(claim, 50)}\"라는 생각은 아직 확인된 근거가 부족합니다."]

    all_text = " ".join([claim] + [e["text"] for e in evidence] + [t.get("answer") or "" for t in turns])
    if any(k in all_text for k in _ACTION_KEYS):
        dtype = "action_needed"
        statement = "걱정되는 마음은 이해되지만, 지금 할 수 있는 작은 일부터 하나씩 해 보면 된다."
        next_action = "오늘 할 수 있는 가장 작은 일 한 가지를 정해서 10분만 해 보기."
    elif not answered:
        dtype = "deferred"
        first = _clip(facts[0].rstrip("."), 40) if facts else "그 일이 있었던 것"
        statement = f"'{first}'는 사실이지만, 그것이 무엇을 뜻하는지는 아직 모른다."
        next_action = None
    else:
        dtype = "reframed"
        first = _clip(facts[0].rstrip("."), 40) if facts else "아쉬운 일이 있었던 것"
        statement = f"'{first}'는 아쉬운 일이었지만, 그 한 번으로 나를 판단할 수는 없다."
        next_action = None

    return DecisionDraft(
        type=dtype,
        reported_facts=facts[:4],
        unsupported_claims=unsupported[:3],
        suggested_statement=statement,
        next_action=next_action,
    )


def rephrase(statement: str, style: str) -> RephrasedStatement:
    base = statement.rstrip(" .")
    if style == "shorter":
        core = re.split(r"[,，]|지만", base)
        text = (core[-1].strip() if len(core) > 1 else base)
        text = _clip(text, 40)
    elif style == "gentler":
        text = f"괜찮아. {base}. 천천히 생각해도 돼"
    else:
        text = base.replace("나를", "나 자신을") if "나를" in base else f"지금 확인된 것만 보면, {base}"
    text = text.rstrip(" .") + "."
    if text.rstrip(".") == statement.rstrip(" ."):
        text = "지금 확인된 사실만 놓고 보면, " + text
    return RephrasedStatement(suggested_statement=text)
