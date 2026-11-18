"""Run the three graph stages with synthetic data and no API credentials."""

import asyncio
import json

from app.workflow import build_workflow


async def main() -> None:
    graph = build_workflow()
    state = {"thought": "회의에서 말이 꼬였다. 모두 나를 한심하게 봤을 것 같다."}
    case = (await graph.ainvoke({**state, "stage": "prosecution"}))["result"]
    context = {**state, "claim": case["claim"], "evidence": case["evidence"]}
    question = (await graph.ainvoke({**context, "stage": "defense"}))["result"]
    turns = [{"question": question["prompt"], "answer": "실제 반응은 확인하지 못했어요."}]
    decision = (await graph.ainvoke({
        **context, "turns": turns, "stage": "decision",
    }))["result"]
    print(json.dumps({"prosecution": case, "defense": question, "decision": decision},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
