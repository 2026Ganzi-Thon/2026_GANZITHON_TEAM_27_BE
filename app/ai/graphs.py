"""LangGraph 기반 AI 흐름.

명세: "LangGraph는 요청 단위로 실행한다. 기소, 변론 질문, 질문 재표현, 판결, 판결 문장 재표현을
분리하고 Pydantic으로 응답 형식을 확인한다."

각 작업은 generate → validate 노드로 된 작은 그래프이며, 형식 검증에 실패하면 한 번 다시 생성한다.
- 형식 검증이 끝내 실패하면 502(AI_BAD_RESPONSE)
- 공급자 호출 자체가 실패(시간 초과·네트워크)하면 503(AI_UNAVAILABLE)
AI 호출은 DB 트랜잭션 밖에서 수행한다(라우터에서 보장).
"""

import json
import logging
from functools import lru_cache
from typing import Any, Callable, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ValidationError

from app.ai import fallback, prompts
from app.ai.llm import chat_model, provider_name
from app.ai.schemas import (
    DecisionDraft,
    DefenseQuestion,
    ProsecutionDraft,
    RephrasedStatement,
    SimplifiedQuestion,
)
from app.errors import ApiError

logger = logging.getLogger("arithon.ai")

MAX_ATTEMPTS = 2


class TaskSpec:
    def __init__(
        self,
        system: str,
        schema: type[BaseModel],
        render: Callable[[dict], str],
        fallback_fn: Callable[[dict], BaseModel],
        check: Callable[[BaseModel, dict], None] | None = None,
    ):
        self.system = system
        self.schema = schema
        self.render = render
        self.fallback_fn = fallback_fn
        self.check = check


def _j(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


def _check_question(out: DefenseQuestion, payload: dict) -> None:
    asked = {p.get("prompt") for p in payload.get("previous", [])}
    if out.prompt in asked:
        raise ValueError("이전 질문과 같은 질문")


def _check_simplify(out: SimplifiedQuestion, payload: dict) -> None:
    if out.prompt.strip() in {p.strip() for p in payload.get("history", [])}:
        raise ValueError("이전 표현과 같은 문장")


def _check_rephrase(out: RephrasedStatement, payload: dict) -> None:
    if out.suggested_statement.strip() == payload["statement"].strip():
        raise ValueError("이전 문장과 같은 문장")


TASKS: dict[str, TaskSpec] = {
    "prosecution": TaskSpec(
        prompts.PROSECUTION,
        ProsecutionDraft,
        lambda p: "사용자가 접수한 글:\n" + p["thought"] + (
            f"\n\n접수 감정: {p['emotion']}" if p.get("emotion") else ""
        ),
        lambda p: fallback.prosecution(p["thought"]),
    ),
    "defense_question": TaskSpec(
        prompts.DEFENSE,
        DefenseQuestion,
        lambda p: (
            f"확정된 주장: {p['claim']}\n확정된 사고 패턴: {p.get('pattern_tags') or '없음'}\n"
            f"확정된 근거:\n{_j(p['evidence'])}\n\n앞선 질문과 답변(answer_status 포함):\n{_j(p['previous'])}\n\n"
            f"이번 질문 순번: {len(p['previous']) + 1} / 3"
        ),
        lambda p: fallback.defense_question(p["claim"], p["evidence"], p["previous"]),
        _check_question,
    ),
    "simplify": TaskSpec(
        prompts.SIMPLIFY,
        SimplifiedQuestion,
        lambda p: f"현재 질문: {p['prompt']}\n이전 표현들: {_j(p['history'])}",
        lambda p: fallback.simplify(p["prompt"], p["revision"]),
        _check_simplify,
    ),
    "decision": TaskSpec(
        prompts.DECISION,
        DecisionDraft,
        lambda p: (
            f"원래 접수 글: {p['thought']}\n확정된 주장: {p['claim']}\n확정된 사고 패턴: {p.get('pattern_tags') or '없음'}\n"
            f"확정된 근거:\n{_j(p['evidence'])}\n\n변론 질문과 답변:\n{_j(p['turns'])}"
        ),
        lambda p: fallback.decision(p["claim"], p["evidence"], p["turns"]),
    ),
    "rephrase": TaskSpec(
        prompts.REPHRASE,
        RephrasedStatement,
        lambda p: f"style: {p['style']}\n현재 제안 문장: {p['statement']}\n판결 유형: {p.get('type')}",
        lambda p: fallback.rephrase(p["statement"], p["style"]),
        _check_rephrase,
    ),
}


class GenState(TypedDict, total=False):
    task: str
    payload: dict
    attempts: int
    raw: Any
    result: BaseModel | None
    error: str | None  # None | "bad" | "unavailable"
    feedback: str | None


def _generate(state: GenState) -> GenState:
    spec = TASKS[state["task"]]
    attempts = state.get("attempts", 0) + 1
    model = chat_model()
    if model is None:
        return {"attempts": attempts, "raw": spec.fallback_fn(state["payload"]), "error": None}

    messages = [SystemMessage(spec.system), HumanMessage(spec.render(state["payload"]))]
    if state.get("feedback"):
        messages.append(HumanMessage(f"직전 응답이 규칙에 맞지 않았다({state['feedback']}). 규칙에 맞게 다시 작성해라."))
    try:
        structured = model.with_structured_output(spec.schema)
        raw = structured.invoke(messages)
        return {"attempts": attempts, "raw": raw, "error": None}
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("AI 형식 오류 task=%s attempt=%s: %s", state["task"], attempts, type(exc).__name__)
        return {"attempts": attempts, "raw": None, "error": "bad", "feedback": "JSON 형식 오류"}
    except Exception as exc:  # 네트워크·시간 초과·인증 등 공급자 장애
        logger.error("AI 호출 실패 task=%s: %s", state["task"], type(exc).__name__)
        return {"attempts": attempts, "raw": None, "error": "unavailable"}


def _validate(state: GenState) -> GenState:
    if state.get("error"):
        return {}
    spec = TASKS[state["task"]]
    raw = state.get("raw")
    try:
        if raw is None:
            raise ValueError("빈 응답")
        data = raw.model_dump() if isinstance(raw, BaseModel) else raw
        result = spec.schema.model_validate(data)
        if spec.check:
            spec.check(result, state["payload"])
        return {"result": result, "error": None}
    except (ValidationError, ValueError) as exc:
        return {"result": None, "error": "bad", "feedback": str(exc)[:200]}


def _route(state: GenState) -> str:
    if state.get("result") is not None:
        return END
    if state.get("error") == "bad" and state.get("attempts", 0) < MAX_ATTEMPTS:
        return "generate"
    return END


@lru_cache
def _graph():
    g = StateGraph(GenState)
    g.add_node("generate", _generate)
    g.add_node("validate", _validate)
    g.set_entry_point("generate")
    g.add_edge("generate", "validate")
    g.add_conditional_edges("validate", _route, {"generate": "generate", END: END})
    return g.compile()


def run_task(task: str, payload: dict) -> BaseModel:
    final = _graph().invoke({"task": task, "payload": payload, "attempts": 0})
    result = final.get("result")
    if result is not None:
        return result
    if final.get("error") == "unavailable":
        raise ApiError(503, "AI_UNAVAILABLE", "AI가 잠시 응답하지 못했어요. 다시 시도해 주세요.")
    # 규칙 기반 생성기의 결과가 검증에 실패하는 경우는 없도록 설계했지만, 그래도 실패하면 502
    if provider_name() == "fallback":
        logger.error("fallback 결과 검증 실패 task=%s", task)
    raise ApiError(502, "AI_BAD_RESPONSE", "AI 응답 형식이 올바르지 않아요. 다시 시도해 주세요.")


def generate_prosecution(thought: str, emotion: dict | None) -> ProsecutionDraft:
    return run_task("prosecution", {"thought": thought, "emotion": emotion})  # type: ignore[return-value]


def generate_question(claim: str, pattern_tags: list, evidence: list[dict], previous: list[dict]) -> DefenseQuestion:
    return run_task(  # type: ignore[return-value]
        "defense_question", {"claim": claim, "pattern_tags": pattern_tags, "evidence": evidence, "previous": previous}
    )


def generate_simplified(prompt: str, history: list[str], revision: int) -> SimplifiedQuestion:
    return run_task("simplify", {"prompt": prompt, "history": history, "revision": revision})  # type: ignore[return-value]


def generate_decision(thought: str, claim: str, pattern_tags: list, evidence: list[dict], turns: list[dict]) -> DecisionDraft:
    return run_task(  # type: ignore[return-value]
        "decision",
        {"thought": thought, "claim": claim, "pattern_tags": pattern_tags, "evidence": evidence, "turns": turns},
    )


def generate_rephrase(statement: str, style: str, dtype: str | None) -> RephrasedStatement:
    return run_task("rephrase", {"statement": statement, "style": style, "type": dtype})  # type: ignore[return-value]
