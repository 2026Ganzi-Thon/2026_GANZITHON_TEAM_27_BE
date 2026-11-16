import uuid

from app.models import CasePost
from app.db import SessionLocal

THOUGHT = "회의에서 발표하다 말이 꼬였다. 다들 나를 한심하게 봤을 거다."


def _create(client, demo, thought=THOUGHT, key=None, emotion=True):
    body = {"thought": thought}
    if emotion:
        body["emotion"] = {"type": "anxiety", "intensity": 80}
    headers = dict(demo)
    if key:
        headers["Idempotency-Key"] = key
    return client.post("/api/v1/cases", json=body, headers=headers)


def _to_closed(client, demo):
    r = _create(client, demo)
    cid = r.json()["case"]["id"]
    draft = client.post(f"/api/v1/cases/{cid}/prosecution", headers=demo).json()
    client.put(
        f"/api/v1/cases/{cid}/evidence",
        json={"claim": draft["claim"], "pattern_tags": draft["pattern_candidates"], "evidence": draft["evidence"]},
        headers=demo,
    )
    q = client.post(f"/api/v1/cases/{cid}/defense/questions", headers=demo).json()
    client.put(
        f"/api/v1/cases/{cid}/defense/questions/{q['id']}/answer",
        json={"action": "skip", "revision": q["revision"]},
        headers=demo,
    )
    d = client.post(f"/api/v1/cases/{cid}/decision", headers=demo).json()
    client.put(
        f"/api/v1/cases/{cid}/decision/final",
        json={"decision_type": d["decision"]["type"], "final_statement": "괜찮다."},
        headers=demo,
    )
    return cid


def test_health(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health").json()["db"] == "ok"


def test_demo_id_required(client):
    r = client.get("/api/v1/cases")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INVALID_DEMO_ID"
    r = client.get("/api/v1/cases", headers={"X-Demo-Id": "not-a-uuid"})
    assert r.status_code == 422


def test_full_court_flow(client, demo):
    r = _create(client, demo, key="k1")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["outcome"] == "case_created"
    case = body["case"]
    assert case["status"] == "received"
    assert case["case_number"].endswith("-001")
    cid = case["id"]

    # 같은 키·같은 본문 → 같은 응답, 다른 본문 → 409
    again = _create(client, demo, key="k1")
    assert again.status_code == 201 and again.json()["case"]["id"] == cid
    mismatch = _create(client, demo, thought="다른 내용", key="k1")
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "IDEMPOTENCY_MISMATCH"

    # 기소
    r = client.post(f"/api/v1/cases/{cid}/prosecution", headers=demo)
    assert r.status_code == 200, r.text
    draft = r.json()
    assert draft["status"] == "evidence_review"
    assert draft["title"].endswith("사건")
    assert draft["evidence"]
    assert "mind_reading" in draft["pattern_candidates"]
    # 재시도 → 저장된 초안
    assert client.post(f"/api/v1/cases/{cid}/prosecution", headers=demo).json()["title"] == draft["title"]

    # 변론은 근거 확정 전에는 409
    r = client.post(f"/api/v1/cases/{cid}/defense/questions", headers=demo)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_CASE_STATE"

    r = client.put(
        f"/api/v1/cases/{cid}/evidence",
        json={
            "claim": draft["claim"],
            "pattern_tags": ["mind_reading"],
            "evidence": [
                {"type": "observed", "text": "발표 중 말이 꼬였다."},
                {"type": "interpretation", "text": "모두 나를 한심하게 봤을 것이라고 추측했다."},
            ],
        },
        headers=demo,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "defense"

    # 질문 1 → 쉽게 바꾸기 → 답변
    r = client.post(f"/api/v1/cases/{cid}/defense/questions", headers=demo)
    assert r.status_code == 201, r.text
    q1 = r.json()
    assert q1["sequence"] == 1 and q1["revision"] == 0 and len(q1["suggested_answers"]) == 3
    # 미응답 질문이 있으면 같은 질문 200
    r = client.post(f"/api/v1/cases/{cid}/defense/questions", headers=demo)
    assert r.status_code == 200 and r.json()["id"] == q1["id"]

    # 판결은 미응답 질문이 있으면 409
    assert client.post(f"/api/v1/cases/{cid}/decision", headers=demo).status_code == 409

    s = client.post(f"/api/v1/cases/{cid}/defense/questions/{q1['id']}/simplify", json={"revision": 0}, headers=demo)
    assert s.status_code == 200, s.text
    assert s.json()["revision"] == 1
    # 오래된 revision 으로 답하면 409
    r = client.put(
        f"/api/v1/cases/{cid}/defense/questions/{q1['id']}/answer",
        json={"action": "answer", "text": "부정적인 말은 듣지 못했어요.", "revision": 0},
        headers=demo,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_REVISION"
    r = client.put(
        f"/api/v1/cases/{cid}/defense/questions/{q1['id']}/answer",
        json={"action": "answer", "text": "부정적인 말은 듣지 못했어요.", "revision": 1},
        headers=demo,
    )
    assert r.status_code == 200, r.text
    assert r.json()["can_request_decision"] is True

    # 질문 2(not_sure), 질문 3(skip)
    for action in ("not_sure", "skip"):
        q = client.post(f"/api/v1/cases/{cid}/defense/questions", headers=demo).json()
        r = client.put(
            f"/api/v1/cases/{cid}/defense/questions/{q['id']}/answer",
            json={"action": action, "revision": q["revision"]},
            headers=demo,
        )
        assert r.status_code == 200, r.text
    r = client.post(f"/api/v1/cases/{cid}/defense/questions", headers=demo)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DEFENSE_COMPLETED"

    detail = client.get(f"/api/v1/cases/{cid}", headers=demo).json()
    assert len(detail["defense"]) == 3
    assert detail["defense"][0]["prompts"][1] == s.json()["prompt"]
    assert [t["answer_status"] for t in detail["defense"]] == ["answered", "not_sure", "skipped"]

    # 판결
    r = client.post(f"/api/v1/cases/{cid}/decision", headers=demo)
    assert r.status_code == 200, r.text
    dec = r.json()
    assert dec["status"] == "decision_review"
    version = dec["decision"]["version"]
    assert dec["decision"]["suggested_statement"]

    r = client.post(f"/api/v1/cases/{cid}/decision/rephrase", json={"style": "gentler", "version": version - 1}, headers=demo)
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_CONFLICT"
    r = client.post(f"/api/v1/cases/{cid}/decision/rephrase", json={"style": "gentler", "version": version}, headers=demo)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == version + 1

    # 확정: reframed 는 final_statement 필수
    r = client.put(f"/api/v1/cases/{cid}/decision/final", json={"decision_type": "reframed"}, headers=demo)
    assert r.status_code == 422
    r = client.put(
        f"/api/v1/cases/{cid}/decision/final",
        json={
            "decision_type": "reframed",
            "final_statement": "발표는 아쉬웠지만 동료들의 평가는 아직 모른다.",
            "emotion_after": {"type": "anxiety", "intensity": 40},
        },
        headers=demo,
    )
    assert r.status_code == 200, r.text
    closed = r.json()
    assert closed["status"] == "closed"
    assert closed["decision"]["final_statement"].startswith("발표는")
    assert closed["decision"]["suggested_statement"] != closed["decision"]["final_statement"]
    assert closed["emotion_after"] == {"type": "anxiety", "intensity": 40}

    # 작은 행동
    r = client.put(f"/api/v1/cases/{cid}/follow-up", json={"choice": "action"}, headers=demo)
    assert r.status_code == 422
    r = client.put(f"/api/v1/cases/{cid}/follow-up", json={"choice": "action", "action_text": "5분 산책하기"}, headers=demo)
    assert r.status_code == 200 and r.json()["follow_up"]["action_text"] == "5분 산책하기"
    r = client.put(f"/api/v1/cases/{cid}/follow-up", json={"choice": "rest"}, headers=demo)
    assert r.json()["follow_up"] == {**r.json()["follow_up"], "choice": "rest", "action_text": None}

    # 목록·통계
    lst = client.get("/api/v1/cases", headers=demo).json()
    assert lst["items"][0]["id"] == cid and lst["next_cursor"] is None
    assert client.get("/api/v1/cases?status=closed&pattern=mind_reading", headers=demo).json()["items"]
    stats = client.get("/api/v1/cases/stats?from=2000-01-01&to=2100-01-01&timezone=Asia/Seoul", headers=demo).json()
    assert stats["closed_count"] == 1
    assert stats["pattern_counts"] == {"mind_reading": 1}
    assert stats["emotion_change"] == {"average": -40.0, "count": 1}

    # 다른 체험 ID 는 볼 수 없다
    other = {"X-Demo-Id": str(uuid.uuid4())}
    assert client.get(f"/api/v1/cases/{cid}", headers=other).status_code == 404
    assert client.get("/api/v1/cases", headers=other).json()["items"] == []


def test_support_required_on_intake(client, demo):
    r = _create(client, demo, thought="요즘 너무 힘들어서 죽고 싶어요")
    assert r.status_code == 200
    assert r.json()["outcome"] == "support_required"
    assert r.json()["support"]["contacts"]
    assert client.get("/api/v1/cases", headers=demo).json()["items"] == []


def test_support_required_mid_defense(client, demo):
    cid = _create(client, demo).json()["case"]["id"]
    draft = client.post(f"/api/v1/cases/{cid}/prosecution", headers=demo).json()
    client.put(
        f"/api/v1/cases/{cid}/evidence",
        json={"claim": draft["claim"], "pattern_tags": [], "evidence": draft["evidence"]},
        headers=demo,
    )
    q = client.post(f"/api/v1/cases/{cid}/defense/questions", headers=demo).json()
    r = client.put(
        f"/api/v1/cases/{cid}/defense/questions/{q['id']}/answer",
        json={"action": "answer", "text": "그냥 자해하고 싶어요", "revision": 0},
        headers=demo,
    )
    assert r.json()["outcome"] == "support_required"
    detail = client.get(f"/api/v1/cases/{cid}", headers=demo).json()
    assert detail["status"] == "support_required"
    assert detail["defense"][0]["answer"] is None


def test_pagination(client, demo):
    for i in range(5):
        assert _create(client, demo, thought=f"생각 {i}", emotion=False).status_code == 201
    page1 = client.get("/api/v1/cases?limit=2", headers=demo).json()
    assert len(page1["items"]) == 2 and page1["next_cursor"]
    page2 = client.get(f"/api/v1/cases?limit=2&cursor={page1['next_cursor']}", headers=demo).json()
    page3 = client.get(f"/api/v1/cases?limit=2&cursor={page2['next_cursor']}", headers=demo).json()
    ids = [c["id"] for c in page1["items"] + page2["items"] + page3["items"]]
    assert len(set(ids)) == 5 and page3["next_cursor"] is None
    numbers = sorted(c["case_number"] for c in client.get("/api/v1/cases", headers=demo).json()["items"])
    assert numbers[-1].endswith("-005")


def test_board(client, demo):
    cid = _to_closed(client, demo)
    # 종결 전 사건은 공유 불가
    open_cid = _create(client, demo).json()["case"]["id"]
    r = client.post(f"/api/v1/cases/{open_cid}/posts", json={"visibility": "public", "title": "t", "body": "b"}, headers=demo)
    assert r.status_code == 409

    r = client.post(
        f"/api/v1/cases/{cid}/posts",
        json={"visibility": "public", "title": "회의 발표가 마음에 걸린 날", "body": "한 번의 실수로 판단할 수 없다.", "tags": ["발표", "직장"]},
        headers=demo,
    )
    assert r.status_code == 201, r.text
    public_id = r.json()["id"]
    assert r.json()["status"] == "pending_review"

    r = client.post(
        f"/api/v1/cases/{cid}/posts",
        json={"visibility": "password_protected", "title": "비밀 글", "body": "비밀 본문"},
        headers=demo,
    )
    assert r.status_code == 422
    r = client.post(
        f"/api/v1/cases/{cid}/posts",
        json={"visibility": "password_protected", "title": "비밀 글", "body": "비밀 본문", "password": "secret12"},
        headers=demo,
    )
    secret_id = r.json()["id"]

    other = {"X-Demo-Id": str(uuid.uuid4())}
    # 검토 전에는 목록에 없고 다른 사람은 상세도 404
    assert client.get("/api/v1/board/posts", headers=other).json()["items"] == []
    assert client.get(f"/api/v1/board/posts/{public_id}", headers=other).status_code == 404
    mine = client.get("/api/v1/board/posts/mine", headers=demo).json()["items"]
    assert {p["id"] for p in mine} == {public_id, secret_id}
    assert client.get(f"/api/v1/board/posts/{public_id}", headers=demo).status_code == 200

    # 운영자 승인 (Supabase 관리 화면 대신 DB 직접 변경)
    with SessionLocal() as db:
        for pid in (public_id, secret_id):
            post = db.get(CasePost, uuid.UUID(pid))
            post.status = "published"
            from app.models import utcnow

            post.published_at = utcnow()
        db.commit()

    items = client.get("/api/v1/board/posts", headers=other).json()["items"]
    assert [p["id"] for p in items] == [public_id]  # 보호 글은 목록 제외
    assert client.get("/api/v1/board/posts?tag=발표", headers=other).json()["items"]
    assert client.get("/api/v1/board/posts?tag=mind_reading&tag=없는태그", headers=other).json()["items"]
    assert client.get("/api/v1/board/posts?q=실수", headers=other).json()["items"]
    assert client.get("/api/v1/board/posts?q=없는단어", headers=other).json()["items"] == []

    # 보호 글: 상세 404, unlock 틀리면 404, 맞으면 200 + no-store
    assert client.get(f"/api/v1/board/posts/{secret_id}", headers=other).status_code == 404
    r = client.post(f"/api/v1/board/posts/{secret_id}/unlock", json={"password": "wrong"}, headers=other)
    assert r.status_code == 404
    r = client.post(f"/api/v1/board/posts/{secret_id}/unlock", json={"password": "secret12"}, headers=other)
    assert r.status_code == 200 and r.json()["body"] == "비밀 본문"
    assert r.headers["cache-control"] == "no-store"
    assert "password_hash" not in r.json()

    # 공감·댓글·신고
    assert client.post(f"/api/v1/board/posts/{public_id}/like", headers=other).json() == {"is_liked": True, "like_count": 1}
    r = client.post(f"/api/v1/board/posts/{public_id}/comments", json={"content": "저도 그래요"}, headers=other)
    assert r.status_code == 201
    detail = client.get(f"/api/v1/board/posts/{public_id}", headers=other).json()
    assert detail["like_count"] == 1 and detail["is_liked"] and detail["comment_count"] == 1
    assert detail["content"] == detail["body"]
    assert client.post(f"/api/v1/board/posts/{public_id}/reports", json={"reason": "other"}, headers=other).status_code == 201
    assert client.post(f"/api/v1/board/posts/{public_id}/reports", json={"reason": "other"}, headers=other).status_code == 200

    # 남의 글 삭제 불가, 사건 삭제 시 글도 삭제
    assert client.delete(f"/api/v1/board/posts/{public_id}", headers=other).status_code == 404
    assert client.delete(f"/api/v1/cases/{cid}", headers=demo).status_code == 204
    assert client.get(f"/api/v1/board/posts/{public_id}", headers=demo).status_code == 404
    assert client.get("/api/v1/board/posts/mine", headers=demo).json()["items"] == []


def test_unlock_rate_limit(client, demo):
    r = client.post("/api/v1/board/posts", json={"visibility": "password_protected", "title": "t", "body": "b", "password": "abcdef"}, headers=demo)
    pid = r.json()["id"]
    other = {"X-Demo-Id": str(uuid.uuid4())}
    codes = [client.post(f"/api/v1/board/posts/{pid}/unlock", json={"password": "x"}, headers=other).status_code for _ in range(6)]
    assert codes[-1] == 429
