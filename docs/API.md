# 생각법정 백엔드 API (전시용 v1.1 구현본)

노션 `BE 개발환경 › 전시용 api명세서`(X-Demo-Id, 로그인 없음)와 `FE 개발환경 › backend-requests`의 요청을 반영한 실제 계약이다.

- Base URL: `http://localhost:8080/api/v1` (Render: `https://<service>.onrender.com/api/v1`)
- `/health`, `/health/live`, `/docs`, `/openapi.json` 은 `/api/v1` 밖이다.
- 모든 `/api/v1` 요청에 `X-Demo-Id: <UUID v4>` 헤더 필수. 누락/형식 오류 → `422` `error.code = "INVALID_DEMO_ID"`.
- 시간은 UTC ISO 8601 (`2026-11-04T12:00:00Z`). ID 는 UUID.
- CORS: `CORS_ORIGINS`(기본 `http://localhost:5173`) + `https://*.vercel.app`. 허용 헤더 `X-Demo-Id`, `Idempotency-Key`, `Content-Type`.

## 공통 형식

오류
```json
{ "error": { "code": "INVALID_CASE_STATE", "message": "...", "request_id": "req_ab12cd34" } }
```

| HTTP | code |
| --- | --- |
| 404 | `NOT_FOUND` |
| 409 | `INVALID_CASE_STATE`, `STALE_REVISION`, `VERSION_CONFLICT`, `IDEMPOTENCY_MISMATCH`, `DEFENSE_COMPLETED`, `SIMPLIFY_LIMIT` |
| 422 | `VALIDATION_ERROR`, `INVALID_DEMO_ID` |
| 429 | `RATE_LIMITED` |
| 502 | `AI_BAD_RESPONSE` |
| 503 | `AI_UNAVAILABLE`, `DB_UNAVAILABLE` |

지원 안내 (HTTP 200) — `POST /cases`, 변론 답변, 판결 확정, 작은 행동, 게시글 작성에서 올 수 있다.
```json
{
  "outcome": "support_required",
  "support": {
    "message": "지금은 재판보다 도움을 받는 것이 먼저예요. 아래 전화로 이야기해 보세요.",
    "contacts": ["정신건강 위기상담전화 1577-0199", "자살예방 상담전화 109"]
  }
}
```
- 접수에서 전환되면 사건을 만들지 않는다.
- 변론 답변·판결 확정(종결 전)에서 전환되면 사건 status 가 `support_required` 로 바뀌고 입력 원문은 저장하지 않는다.
- 작은 행동·게시글(종결 후)에서 전환되면 그 요청만 중단되고 사건은 그대로다.

## Enum

| 구분 | 값 |
| --- | --- |
| case status | `received`, `evidence_review`, `defense`, `decision_review`, `closed`, `support_required` |
| emotion.type | `anxiety`, `sadness`, `anger`, `shame`, `guilt`, `other` (intensity 0~100 정수, type·intensity 둘 다 또는 둘 다 생략) |
| pattern | `all_or_nothing`, `overgeneralization`, `mind_reading`, `catastrophizing`, `labeling`, `discounting_positives` |
| evidence.type | `observed`, `reported`, `interpretation`, `unknown` |
| answer action / answer_status | `answer`, `not_sure`, `skip` / `pending`, `answered`, `not_sure`, `skipped` |
| decision type | `reframed`, `deferred`, `action_needed` |
| rephrase style | `neutral`, `shorter`, `gentler` |
| follow-up choice | `action`, `rest` |
| post visibility / status | `public`, `password_protected` / `pending_review`, `published`, `hidden` |
| report reason | `personal_information`, `harmful_content`, `other` |

## 사건

### `POST /cases`
헤더 `Idempotency-Key` (선택, 권장). 본문 `{ "thought": "...", "emotion": { "type": "anxiety", "intensity": 80 } }` (`emotion` 생략 가능)

`201`
```json
{ "outcome": "case_created", "case": { "id": "uuid", "case_number": "2026-001", "status": "received", "title": null, "thought": "...", "emotion": {"type":"anxiety","intensity":80}, "emotion_after": null, "created_at": "..." } }
```
- `case_number` 는 체험 ID 별로 `연도-001` 부터 증가한다.
- 같은 체험 ID·같은 키·같은 본문 재전송 → 처음과 같은 상태코드/본문. 같은 키·다른 본문 → `409 IDEMPOTENCY_MISMATCH`.

### `GET /cases?status=&pattern=&limit=20&cursor=`
`200 { "items": [CaseListItem], "next_cursor": "opaque" | null }` — `created_at DESC, id DESC`.

CaseListItem
```json
{ "id": "uuid", "case_number": "2026-001", "status": "closed", "title": "회의 발표 사건", "thought": "...", "summary": "...",
  "pattern_tags": ["mind_reading"], "emotion": {...} | null, "emotion_after": {...} | null,
  "final_statement": "..." | null, "created_at": "...", "closed_at": "..." | null }
```

### `GET /cases/stats?from=YYYY-MM-DD&to=YYYY-MM-DD&timezone=Asia/Seoul`
`200 { "closed_count": 3, "pattern_counts": {"mind_reading": 2}, "emotion_change": { "average": -25.0 | null, "count": 2 } }`
(`average` = 종료 강도 − 접수 강도 평균. 줄어들면 음수.)

### `GET /cases/{id}` — 사건 상세 (이어하기·감정 변화·요약 공용)
```json
{
  "id": "uuid", "case_number": "2026-001", "status": "closed", "version": 7,
  "title": "회의 발표 사건", "thought": "원문", "claim": "...", "summary": "40자 이내 요약",
  "pattern_candidates": ["mind_reading","labeling"], "pattern_tags": ["mind_reading"],
  "evidence": [{ "type": "observed", "text": "발표 중 말이 꼬였다." }],
  "emotion": { "type": "anxiety", "intensity": 80 }, "emotion_after": { "type": "anxiety", "intensity": 30 },
  "defense": [
    { "id": "uuid", "sequence": 1, "prompt": "최신 질문 문장", "revision": 1,
      "prompts": ["처음 문장", "쉽게 바꾼 문장"], "suggested_answers": [{"rank":1,"text":"..."}],
      "answer_status": "answered", "answer": "답변" | null, "answered_at": "..." | null }
  ],
  "open_question": { ...defense 항목... } | null,
  "decision": {
    "type": "deferred",               // 확정 전에는 초안 유형, 확정 후에는 확정 유형
    "draft_type": "deferred", "final_type": "deferred" | null,
    "reported_facts": [], "unsupported_claims": [],
    "suggested_statement": "AI 초안", "next_action": "..." | null,
    "final_statement": "사용자 확정 문장" | null, "finalized_at": "..." | null,
    "version": 7
  } | null,
  "final_statement": "..." | null,
  "follow_up": { "choice": "action", "action_text": "5분 산책하기", "updated_at": "..." } | null,
  "created_at": "...", "updated_at": "...", "closed_at": "..." | null
}
```

### `DELETE /cases/{id}` → `204` (근거·질문·판결·재시도 기록·게시글 함께 삭제)

## 기소

### `POST /cases/{id}/prosecution`
- `received` → AI 초안 생성 후 `evidence_review` 로 전환.
- 그 이후 상태 → 저장된 초안(확정된 값)을 다시 돌려준다(새로 만들지 않음).

`200`
```json
{ "status": "evidence_review", "title": "회의 발표 사건", "claim": "모두 나를 한심하게 봤을 것이다", "summary": "...",
  "pattern_candidates": ["mind_reading","labeling"], "evidence": [{ "type": "observed", "text": "..." }], "version": 2 }
```

### `PUT /cases/{id}/evidence` (`evidence_review` 에서만)
본문 `{ "claim": "...", "pattern_tags": ["mind_reading"], "evidence": [{"type":"observed","text":"..."}], "title": "선택" }`
→ 근거 전체 교체, status `defense`. `200` = 사건 상세.

## 변론

### `POST /cases/{id}/defense/questions` (`defense` 에서만)
- 미응답 질문이 있으면 `200` 으로 그 질문, 없으면 새 질문 생성 `201`.
- 이미 3개를 처리했으면 `409 DEFENSE_COMPLETED`.

```json
{ "id": "uuid", "sequence": 1, "prompt": "...", "revision": 0, "prompts": ["..."],
  "suggested_answers": [{ "rank": 1, "text": "..." }, { "rank": 2, "text": "..." }, { "rank": 3, "text": "..." }],
  "answer_status": "pending", "answer": null, "remaining": 2 }
```

### `PUT /cases/{id}/defense/questions/{qid}/answer`
본문 `{ "action": "answer" | "not_sure" | "skip", "text": "answer 일 때만", "revision": 0 }`
- revision 불일치 → `409 STALE_REVISION`.
- `200 { "outcome": "answered", "question": {...}, "processed_count": 1, "can_request_decision": true, "remaining": 2 }`

### `POST /cases/{id}/defense/questions/{qid}/simplify`
본문 `{ "revision": 0 }` → `200 { "id": "uuid", "prompt": "쉬운 문장", "revision": 1 }` (질문당 최대 2회, 초과 `409 SIMPLIFY_LIMIT`)

## 판결

### `POST /cases/{id}/decision`
- `defense` 에서 질문 ≥1개 처리 & 미응답 없음 → 초안 생성 후 `decision_review`.
- `decision_review`/`closed` → 저장된 판결.

`200 { "status": "decision_review", "decision": { "type": "deferred", "reported_facts": [], "unsupported_claims": [], "suggested_statement": "...", "next_action": null, "version": 5 } }`

### `POST /cases/{id}/decision/rephrase` (`decision_review`)
본문 `{ "style": "gentler", "version": 5 }` → `200 { "suggested_statement": "...", "version": 6 }` (버전 불일치 `409 VERSION_CONFLICT`)

### `PUT /cases/{id}/decision/final` (`decision_review`)
본문 `{ "decision_type": "reframed", "final_statement": "deferred 일 때만 생략 가능", "emotion_after": {"type":"anxiety","intensity":40} }`
→ `200` 사건 상세(status `closed`). (`version` 을 함께 보내면 충돌 검사)

### `PUT /cases/{id}/follow-up` (`closed`)
본문 `{ "choice": "action", "action_text": "1~500자" }` 또는 `{ "choice": "rest" }`
→ `200 { "follow_up": { "choice": "action", "action_text": "...", "updated_at": "..." } }`

## 게시판

PostItem (목록)
```json
{ "id": "uuid", "title": "...", "body": "...", "tags": ["발표"], "pattern_tags": ["mind_reading"],
  "visibility": "public", "status": "published", "is_mine": false, "is_hot": false,
  "like_count": 3, "comment_count": 1, "created_at": "...", "published_at": "..." }
```
- 비밀번호 보호 글은 남에게 `title`/`body` 를 주지 않는다(`null`).

| 메서드·경로 | 설명 |
| --- | --- |
| `POST /cases/{id}/posts` | `{ visibility, title, body, tags?, password? }` (종결 사건만) → `201 { "id", "status": "pending_review", "visibility", "created_at" }` |
| `POST /board/posts` | (확장) 사건 없이 바로 글쓰기. 본문 동일 |
| `GET /board/posts?tag=&tag=&q=&limit=&cursor=` | 게시된 public 글. `tag` 는 여러 개 가능(하나라도 일치, tags·pattern_tags 모두 검사) |
| `GET /board/posts/mine?limit=&cursor=` | 내 글 전체(검토 상태 포함) |
| `GET /board/posts/{pid}` | 상세. 남의 비공개·검토중·숨김 글은 `404` |
| `POST /board/posts/{pid}/unlock` | `{ password }` → 상세 / `404`. `Cache-Control: no-store`, 체험 ID+글 당 분당 5회 |
| `POST /board/posts/{pid}/reports` | `{ reason }` → `201` / 기존 신고 `200` |
| `DELETE /board/posts/{pid}` | 내 글 삭제 `204` |
| `POST /board/posts/{pid}/like` | (확장) 공감 토글 → `{ "is_liked": true, "like_count": 4 }` |
| `POST /board/posts/{pid}/comments` | (확장) `{ content }` → 댓글 `201` |

PostDetail
```json
{ "id": "uuid", "title": "...", "body": "...", "content": "...(body 와 같음)", "tags": [], "pattern_tags": [],
  "visibility": "public", "status": "published", "author": "익명", "is_mine": true,
  "like_count": 0, "is_liked": false, "comment_count": 0,
  "comments": [{ "id": "uuid", "author": "익명", "content": "...", "created_at": "...", "is_mine": false }],
  "created_at": "...", "published_at": "..." | null }
```

`BOARD_AUTO_PUBLISH=true` 이면 새 글이 바로 `published` 로 저장된다(전시 시연용). 기본은 명세대로 `pending_review` 이며 운영자가 Supabase 화면에서 `status` 를 `published` 로 바꾼다(`published_at` 은 트리거/서버가 채움).
