# 생각법정(체포하겠어) · 전시용 API 명세서 v1.4

2026-11-18 · 프론트 연동 기준. 최신 피그마에서 확인한 댓글·공감·북마크·별도 글쓰기 구성을 반영했다. 경로의 API 버전은 `/api/v1`이다. 서버는 FastAPI, DB는 Supabase PostgreSQL의 `court_demo` 스키마, AI는 LangGraph와 Upstage Solar를 사용한다. 개발 시 모의 AI로 동일한 요청·응답 형식을 실행할 수 있다.

프론트 프로젝트 구조와 화면별 호출 순서는 [9. 프론트엔드 구조와 화면 연결](#9-프론트엔드-구조와-화면-연결)을 참조한다. 자료형·관계는 [ERD](api-erd.md), 기계 판독 요청 스키마는 [OpenAPI](openapi.json)로 제공한다.

## 1. 접속 정보

| 항목 | 값 |
|---|---|
| Vite 로컬 설정 | `VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1` |
| 로컬 Swagger | [Swagger UI](http://127.0.0.1:8000/docs) |
| 로컬 OpenAPI | [OpenAPI JSON](http://127.0.0.1:8000/openapi.json) |
| Render | 배포 후 `https://<서비스명>.onrender.com/api/v1` 사용. 실제 서비스 URL은 배포 환경에서 확인 |
| 필수 공통 헤더 | `X-Demo-Id: <UUID v4>` |
| 접수 전용 헤더 | `Idempotency-Key: <요청마다 생성한 키>` |
| JSON 본문 헤더 | `Content-Type: application/json` |

앱은 체험을 시작할 때 무작위 UUID v4를 만들고 같은 방문자의 요청에 재사용한다. 회원가입, 로그인, `Authorization` 헤더, `GET /me`는 전시 계약에서 제외한다. 공용 기기에서는 새 방문자 체험 시작 시 새 ID를 생성한다. 체험 ID가 없거나 UUID v4가 아니면 `422 INVALID_DEMO_ID`를 반환한다. 형식 오류를 고칠 때에는 기기에 저장된 유효한 ID가 있으면 그 값을 우선 사용한다.

피그마의 로그인·회원가입 화면은 전시 진입 흐름에서 제외하고 홈 또는 동의 안내로 바로 연결한다. 동의 안내를 사용하면 완료 상태는 프론트가 관리한다. 체험 ID는 계정 인증 토큰이 아니며 기기에서 보관하는 전시용 식별자다. 게시글과 댓글에는 체험 ID를 노출하지 않고 작성자를 `익명`으로 표시한다.

브라우저 개발 주소 `http://localhost:5173`과 `http://127.0.0.1:5173`은 기본 허용한다. Vercel 미리보기와 운영 주소는 서버 환경변수 `CORS_ORIGINS`에 스킴을 포함한 정확한 Origin을 쉼표로 구분해 적는다. 허용 헤더는 `X-Demo-Id`, `Idempotency-Key`, `Content-Type`이고 허용 메서드는 GET, POST, PUT, DELETE다. 서버가 OPTIONS 사전 요청을 처리한다.

로컬 테스트는 `POST /cases`에서 만든 `case.id`를 경로에 넣고 같은 체험 ID를 사용한다. 서버 실행과 DB 연결은 각각 `GET /health/live`, `GET /health`에서 확인한다. 두 경로는 `/api/v1`과 체험 ID 없이 호출한다.

## 2. 경로와 상태 전환

아래 경로에는 `/api/v1`을 붙인다. AI 생성 및 조회에 필요한 사건과 대화 내역은 서버가 사건 ID를 기준으로 읽는다.

| 메서드·경로 | 요청 | 정상 응답 |
|---|---|---|
| `POST /cases` | `thought`, `emotion?`, 접수 키 | `201` `{outcome:"case_created",case}` |
| `GET /cases` | `status?`, `pattern?`, `limit?`, `cursor?` | `200` `{items,next_cursor}` |
| `GET /cases/stats` | `from`, `to`, `timezone?` | `200` `{closed_count,pattern_counts,emotion_change}` |
| `GET /cases/{id}` | 사건 ID | `200` `{case,evidence,questions,decision,can_request_decision,defense_completed}` |
| `DELETE /cases/{id}` | 사건 ID | `204` |
| `POST /cases/{id}/prosecution` | 본문 없음 | `200` `{title,claim,summary,pattern_candidates,evidence,status,version}` |
| `PUT /cases/{id}/evidence` | `claim`, `pattern_tags`, `evidence` | `200` 확정한 주장·태그·근거와 `status`, `version` |
| `POST /cases/{id}/defense/questions` | 본문 없음 | `201` 새 질문 또는 `200` 미응답 질문 |
| `PUT /cases/{id}/defense/questions/{question_id}/answer` | `revision`, `action`, `text?` | `200` 처리된 질문 |
| `POST /cases/{id}/defense/questions/{question_id}/simplify` | `revision` | `200` 변경된 질문과 추천 답변 |
| `POST /cases/{id}/decision` | 본문 없음 | `200` `{status,version,decision}` |
| `POST /cases/{id}/decision/rephrase` | `style`, `version` | `200` `{suggested_statement,status,version}` |
| `PUT /cases/{id}/decision/final` | `decision_type`, `final_statement?`, `emotion_after?` | `200` `{case,decision}` |
| `GET /cases/{id}/follow-up/options` | 종결 사건 ID | `200` 추천 1개와 고정 카드 4개 |
| `PUT /cases/{id}/follow-up` | `choice`, `action_text?` | `200` `{choice,action_text,follow_up:{choice,action_text,updated_at}}` |
| `POST /cases/{id}/posts` | `visibility`, `title`, `body`, `tags?`, `password?` | `201` `{id,status}` |
| `POST /board/posts` | `visibility`, `title`, `body`, `tags?`, `password?` | `201` `{id,status}`. 사건 없는 별도 글쓰기 |
| `GET /board/posts` | `tag?`, `q?`, `limit?`, `cursor?` | `200` 공개 게시판 목록 |
| `GET /board/posts/mine` | `limit?`, `cursor?` | `200` 현재 체험에서 작성한 글 목록 |
| `GET /board/posts/{post_id}` | 게시글 ID | `200` 공개 글 또는 현재 체험의 글 |
| `PUT /board/posts/{post_id}` | `title`, `body`, `tags?` | `200` `{id,status:"pending_review"}`. 내 글 수정 |
| `POST /board/posts/{post_id}/unlock` | `password` | `200` 보호 글 본문 |
| `POST /board/posts/{post_id}/reports` | `reason` | `201` 신규 신고 또는 `200` 기존 신고 |
| `DELETE /board/posts/{post_id}` | 게시글 ID | `204` |
| `GET /board/posts/{post_id}/comments` | `limit?`, `cursor?` | `200` `{items,next_cursor}` |
| `POST /board/posts/{post_id}/comments` | `body` | `201` 작성한 댓글 |
| `PUT /board/comments/{comment_id}` | `body` | `200` 수정한 댓글 |
| `DELETE /board/comments/{comment_id}` | 댓글 ID | `204` |
| `PUT /board/posts/{post_id}/like` | 본문 없음 | `200` `{liked:true,like_count}` |
| `DELETE /board/posts/{post_id}/like` | 본문 없음 | `200` `{liked:false,like_count}` |
| `PUT /board/comments/{comment_id}/like` | 본문 없음 | `200` `{liked:true,like_count}` |
| `DELETE /board/comments/{comment_id}/like` | 본문 없음 | `200` `{liked:false,like_count}` |
| `PUT /board/posts/{post_id}/bookmark` | 본문 없음 | `200` `{bookmarked:true}` |
| `DELETE /board/posts/{post_id}/bookmark` | 본문 없음 | `200` `{bookmarked:false}` |
| `GET /board/bookmarks` | `limit?`, `cursor?` | `200` `{items,next_cursor}` |

| 사건 상태 | 화면 및 요청 |
|---|---|
| `received` | 기소 화면에서 `POST /prosecution`으로 초안을 생성한다. |
| `evidence_review` | 상세의 `case`와 `evidence`로 저장된 초안을 표시하고 근거를 확정한다. |
| `defense` | 상세의 `questions`를 그린다. 미응답 질문이 있으면 이어서 답변한다. |
| `decision_review` | 상세의 `decision`과 `case.version`으로 저장된 판결을 표시한다. |
| `closed` | 사례 요약, 확정 문장, 감정 전후, 저장된 작은 행동을 표시한다. |
| `support_required` | 지원 안내 화면을 표시한다. 해당 사건의 목록·상세 조회와 삭제가 가능하다. |

휴정 시 상세 조회로 마지막으로 저장한 단계와 질문을 복원한다. 제출 전 편집은 프론트에서 체험 ID·사건 ID·단계별로 임시 저장한다. AI 생성을 다시 요청해도 기소는 `evidence_review`부터 `closed`까지 저장된 현재 제목·주장·근거를 반환한다. 판결은 `decision_review`, `closed`에서 같은 응답 구조로 저장된 판결을 반환한다.

## 3. 접수·기소·근거

- `thought`는 공백 제거 후 1~2,000자다. `emotion`은 선택값이며 `{type,intensity}` 구조다. `type`은 `anxiety`, `sadness`, `anger`, `shame`, `guilt`, `other` 중 하나다. `intensity`는 0~100 정수다.
- 일반 접수 응답의 `outcome`은 `case_created`다. 같은 체험 ID·본문·키로 24시간 안에 재전송하면 기존 `201` 응답을 반환한다. 같은 키에 입력이 다르면 `409 IDEMPOTENCY_CONFLICT`다.
- 새 사건번호는 체험 ID와 서울 기준 연도별로 `2026-001`부터 발급한다. `999` 이후에는 `1000`으로 늘어난다. 화면의 `#`은 프론트에서 붙이고 API 경로에는 사건 UUID를 사용한다.
- 기소의 `title`은 ‘○○ 사건’ 형식이며 약 15자를 목표로 한다. `claim`은 검토할 주장이고 약 40자의 한 문장을 목표로 한다. `summary`는 **접수 글의 1~40자 요약**이다. 사건 요약의 ‘처음 든 생각’에는 `case.summary`를 사용한다. 이전 데이터에서 이 값이 `null`이면 `case.original_thought`를 한 줄로 표시한다. 근거 확정에서 `claim`을 바꿔도 접수 요약은 그대로다.
- AI의 사고 패턴 후보는 0~2개다. 대표 패턴은 사용자가 확정한 첫 태그인 `case.primary_pattern`이며, 없으면 `null`이다.
- 검사 측 증거에는 `evidence`를 표시한다. 사용자가 접수 글에 직접 포함한 최근 또는 과거 경험을 사용할 수 있다. 기소 초안은 최대 4개, 항목당 80자 이내이며 짧은 문장을 생성하도록 요청한다. 근거 확정은 최대 20개, 항목당 2,000자다. 유형은 `observed`(직접 경험), `reported`(들은 말), `interpretation`(해석), `unknown`(미확인)이다.

| 화면 표시 | 서버 enum |
|---|---|
| 흑백논리 | `all_or_nothing` |
| 과잉 일반화 | `overgeneralization` |
| 파국화 | `catastrophizing` |
| 낙인찍기 | `labeling` |
| 독심술 | `mind_reading` |
| 감정적 추론 | `emotional_reasoning` |
| 당위적 사고 | `should_statements` |
| 개인화 | `personalization` |
| 타인비난 | `blaming` |
| 비교하기 | `comparing` |
| 긍정 격하 | `discounting_positives` |

서버는 디자인의 10종과 기존 ‘긍정 격하’를 합해 11종을 지원한다. 프론트의 표시명 매핑에도 11종을 등록한다. 선택 UI는 디자인의 10종으로 구성할 수 있다.

## 4. 변론·재표현·판결

1. 첫 질문과 다음 질문 모두 `POST /defense/questions`에서 받는다. 아직 처리하지 않은 질문이 있으면 같은 질문을 받는다.
2. 질문마다 `id`, `sequence`, `prompt`, `revision`, `prompts`, `suggested_answers`, `answer`, `answer_status`, `answered_at`이 있다. `suggested_answers`는 `rank` 1~3 순서이며 새 질문이나 재표현은 3개를 반환한다. 이전 서버에서 저장한 질문의 추천은 빈 배열일 수 있다.
3. 추천 답변을 누르면 해당 `text`를 최신 `revision`과 함께 `action:"answer"`로 보낸다. `not_sure`와 `skip`은 `text`를 생략한다. 쉽게 바꾸기를 요청하면 질문 ID·순번을 유지하고 `revision`이 1 증가한다. `prompts`의 마지막 항목이 최신 문장이다. 같은 질문은 두 번까지 쉽게 바꿀 수 있다.
4. 세 질문이 처리되고 변론 중이면 추가 생성에 `409 DEFENSE_COMPLETED`가 온다. 상세의 `defense_completed`는 세 질문 모두 처리 여부다. `can_request_decision`은 현재 변론 화면에서 판결을 요청할 수 있는지 나타낸다. 최소 한 질문을 처리하고 미응답 질문이 없으면 중간에도 판결로 넘어갈 수 있다.
5. 판결 초안과 상세의 `decision.type`은 AI가 제안한 유형이다. `reported_facts`, `unsupported_claims`, `suggested_statement`, `next_action`을 표시할 수 있다. 별도 표시 문구와 점수 필드는 없다. 유형은 `reframed`, `deferred`, `action_needed`다. 사용자가 확정한 값은 `decision.final_type`, `decision.final_statement`로 읽는다.
6. 재표현은 `POST /decision` 응답의 최상위 `version` 또는 상세의 `case.version`을 보낸다. `style`은 `neutral`, `shorter`, `gentler` 중 하나다. 응답의 새 버전으로 다음 재표현을 요청한다. 서버가 보관한 초안만 수정되며 사용자가 제출하지 않은 문장은 기기에 임시 저장한다.
7. 화면 순서는 판결 → 감정 변화 → 종결 → 사건 요약 → 행동 제안이다. 감정 변화 화면의 다음 버튼에서 `PUT /decision/final`로 유형·문장·선택 감정을 함께 보낸다. `deferred`일 때 문장을 생략할 수 있다. 상세의 `case.emotion`과 `case.emotion_after`는 `{type,intensity}` 또는 `null`이다. 기존 평면 필드 `emotion_before_type`, `emotion_before_intensity`, `emotion_after_type`, `emotion_after_intensity`도 반환한다.

## 5. 판결 이후·보관함·게시판

- `GET /follow-up/options`는 종결 후 호출한다. 판결의 `next_action`을 사용할 수 있으면 `recommended.description`에 담고, 없으면 가볍게 걷기를 추천한다. `options`는 `{id,title,description,tag}` 구조의 카드 4개다. 선택한 설명은 `{"choice":"action","action_text":"..."}`로 저장한다. `{"choice":"rest"}`로 쉬기를 저장한다. 직접 입력은 1~500자다. 상세 복원 시 `decision.follow_up_choice`, `decision.follow_up_action`, `decision.follow_up_updated_at`을 읽는다.
- 보관함은 현재 체험에서 만든 사건만 반환한다. 목록 기본 20개, 최대 50개이며 `next_cursor`를 다음 호출에 전달한다. 통계는 `from` 포함, `to` 제외의 날짜 범위에서 종결일을 비교한다. 기본 시간대는 `Asia/Seoul`이다. 확정한 태그로 집계하며, 같은 감정을 전후에 기록한 사건만 평균에 포함한다. `emotion_change.average`는 **종료 점수 − 접수 점수**다. 80→50은 ‘30점 줄었어요’에 해당한다. 표본이 없으면 `{count:0,average:null}`이다.
- 사건 카드는 프론트에서 선택한 문장을 미리 보여주고 PNG로 저장·공유한다.

### 5.1. 사건 공유와 별도 글쓰기

| 진입 | 호출 | 사건 연결 |
|---|---|---|
| 종결 화면에서 사건 공유 | `POST /cases/{id}/posts` | 현재 체험 소유의 종결 사건이 필요하다. |
| 게시판의 글쓰기 | `POST /board/posts` | 사건 없이 작성한다. `case_id`를 요청에 넣지 않는다. |

두 작성 요청의 본문은 같다. `title`은 공백 제거 후 1~120자, `body`는 1~2,000자, `tags`는 최대 5개(각 1~24자)다. 같은 태그는 한 번만 저장한다. `visibility`는 `public` 또는 `password_protected`다. 보호 글에만 8~64자 `password`를 본문으로 보내며 공개 글에서는 생략한다. 비밀번호 원문은 저장하지 않는다.

작성 응답은 `201 {id,status:"pending_review"}`다. 게시판 공개 목록과 검색에는 `visibility="public"`이면서 `status="published"`인 글만 표시한다. 운영자가 Supabase에서 게시 승인·숨김을 처리한다. 내 글 목록은 검토 중·숨김·보호 글도 포함한다. 보호 글은 공개 목록에 노출하지 않고 승인 후 글 링크와 비밀번호로 열람한다. 비공개 글을 공개 목록에 표시하거나 인기순으로 정렬하는 API는 없다.

내 글 수정은 `PUT /board/posts/{post_id}`에 `title`, `body`, 선택 `tags`를 보낸다. 태그 생략·null은 기존 유지, `[]`는 모두 제거다. 수정 후 응답은 `200 {id,status:"pending_review"}`이며 다시 승인받기 전에는 공개 목록과 타인 열람에서 제외된다. 최초 `published_at`은 유지된다. 이 요청은 공개 범위·비밀번호를 변경하지 않는다. 내 글 삭제는 `DELETE /board/posts/{post_id}`다. 다른 체험의 글을 수정·삭제하면 `404 POST_NOT_FOUND`를 반환한다.

원본 사건을 삭제하면 그 사건에 연결된 게시글도 삭제된다. 사건 없는 글은 사건 삭제의 영향을 받지 않는다. 게시글 삭제 시 댓글·글 공감·댓글 공감·북마크·신고·잠금 해제 시도 기록이 함께 삭제된다. 신고 사유는 `personal_information`, `harmful_content`, `other`다.

### 5.2. 댓글·공감·북마크

댓글 조회·작성·수정·삭제, 글·댓글 공감, 북마크는 **승인된 공개 글**에서 사용한다. 검토 중·숨김·비밀번호 보호 글에 대한 상호작용 요청은 작성자도 이용할 수 없다. 승인 상태가 바뀌면 기존 댓글·공감 기록을 삭제하지 않고 다시 승인될 때 조회할 수 있다.

- 댓글 작성·수정 본문은 `{"body":"내용"}`이며 공백 제거 후 1~1,000자다. 댓글 수정·삭제는 현재 체험이 작성한 댓글만 허용한다. 댓글을 삭제하면 그 댓글의 공감도 함께 삭제한다.
- 댓글 목록은 `created_at ASC, id ASC`로 먼저 작성한 순서다. `next_cursor`가 있으면 같은 글의 다음 페이지에 그대로 보낸다. 목록 기본 20개, 최대 50개다.
- 글과 댓글 공감의 `PUT`은 공감 상태를 켜고 `DELETE`는 끈다. 같은 요청을 반복해도 한 체험의 공감은 하나만 유지한다. 응답은 현재 요청의 `liked`와 전체 `like_count`다. 버튼 클릭을 서버에서 반전하는 방식으로 구현하지 않는다.
- 북마크의 `PUT`은 저장, `DELETE`는 해제다. 반복 호출해도 중복 저장하지 않는다. `GET /board/bookmarks`는 현재 체험의 북마크 중 지금 열람할 수 있는 공개 글을 저장한 시각의 최신순으로 반환한다. 글이 숨김·검토 중이면 목록에서 제외되고, 재승인하면 다시 나타난다.
- 댓글에는 답글·대댓글·댓글 신고를 제공하지 않는다. 글 신고는 기존 `POST /board/posts/{post_id}/reports`를 사용한다.

### 5.3. 게시판 응답 필드

게시글 목록·상세·내 글·잠금 해제는 같은 게시글 객체를 반환한다. 기존 `id,title,body,tags,published_at`을 유지하고 아래 표시값을 추가한다. 체험 ID·원본 사건 ID·비밀번호 해시는 반환하지 않는다.

| 필드 | JSON 자료형 | 의미 |
|---|---|---|
| `visibility` | string | `public` 또는 `password_protected` |
| `status` | string | `pending_review`, `published`, `hidden` |
| `created_at` | ISO 8601 string | 글 작성 시각 |
| `author_label` | string | `익명` |
| `is_mine` | boolean | 현재 체험이 작성한 글인지 |
| `like_count` | integer | 글 공감 수 |
| `comment_count` | integer | 삭제되지 않은 댓글 수 |
| `liked_by_me` | boolean | 현재 체험의 글 공감 여부 |
| `bookmarked_by_me` | boolean | 현재 체험의 북마크 여부 |

댓글 객체는 `id`, `post_id`, `body`, `created_at`, `updated_at`, `author_label`, `is_mine`, `like_count`, `liked_by_me`를 반환한다. 댓글 작성자는 항상 `익명`으로 표시하고 ‘수정·삭제’ 메뉴는 `is_mine=true`일 때 표시한다. 목록 UI의 상대 시각은 `created_at`으로 계산한다. 북마크 목록의 게시글 객체에는 `bookmarked_at`을 추가하며 저장 시각 최신순으로 정렬한다. 정확한 신규 응답 구조는 [10. 게시판 요청·응답 예시](#10-게시판-요청응답-예시)를 참조한다.

## 6. 오류·지원 안내·응답 대기

| HTTP | `error.code` | 화면 처리 |
|---|---|---|
| 404 | `CASE_NOT_FOUND`, `QUESTION_NOT_FOUND`, `POST_NOT_FOUND`, `COMMENT_NOT_FOUND` | 목록 또는 해당 화면으로 이동 |
| 409 | `DEFENSE_COMPLETED` | 판결 버튼 활성화 |
| 409 | `INVALID_CASE_STATE`, `VERSION_CONFLICT`, `STALE_REVISION`, `QUESTION_CONFLICT`, `DEFENSE_INCOMPLETE` | 사건 상세를 다시 읽어 화면과 버전을 맞춤 |
| 409 | `IDEMPOTENCY_CONFLICT`, `REVISION_LIMIT`, `STATEMENT_MISSING` | 새 접수 키를 발급하거나 현재 질문·판결을 표시 |
| 422 | `INVALID_DEMO_ID`, `VALIDATION_ERROR`, `INVALID_CURSOR`, `INVALID_DATE_RANGE` | 체험 ID나 입력값 확인 |
| 429 | `UNLOCK_RATE_LIMITED` | 보호 글 비밀번호 재입력 대기 |
| 502 | `AI_INVALID_RESPONSE` | 빈 응답, 잘린 응답, JSON·스키마 오류. 저장 상태 유지 |
| 503 | `AI_UNAVAILABLE`, `DB_UNAVAILABLE`, `PASSWORD_UNAVAILABLE` | 기존 입력을 보존하고 다시 시도 |

현재 429는 보호 글 잠금 해제에 적용한다. 글·체험 ID 조합은 5분에 5회, 글 전체는 5분에 30회까지다. 일반 API 분당 IP 제한은 설정돼 있지 않다.

지원 안내는 HTTP `200`과 `outcome:"support_required"`, `support.message`, `support.contacts` 배열을 반환한다. 연락처는 `{label,number,href}` 객체이며 서버 배열로 화면을 그린다. 발생 경로는 사건 접수, 근거 확정, 변론 답변, 판결 확정, 작은 행동 저장, 게시글 작성·수정, 댓글 작성·수정이다. 접수 단계에서는 사건을 생성하지 않으며, 진행 중인 사건은 지원 안내 상태로 전환한다. 종결 후 행동·게시판 요청은 해당 입력 저장만 중단하고 기존 기록과 사건 상태를 유지한다. 전시용 지원 분기는 명시적 표현을 확인하는 규칙으로 동작한다.

AI 경로는 일반 HTTP 요청 하나를 기다리는 방식이다. Upstage 연결 제한은 5초, 읽기 제한은 30초다. 프론트는 대기 표시와 재시도 처리를 둔다. 실제 모델 응답 시간은 배포 후 측정이 필요하다. Render 무료 환경의 첫 요청에는 기동 시간이 추가될 수 있어, 시연 직전에 `/health/live`와 `/health`를 확인한다. [Render 무료 Web Service 문서](https://render.com/docs/free#spinning-down-on-idle)

## 7. DB 적용·검증

신규 전시 DB에는 [schema-demo.sql](../db/schema-demo.sql)을 한 번 적용한다. 기존 전시 DB는 적용 이력을 확인해 아래에서 빠진 것만 순서대로 실행하고 v1.4 서버를 배포한다. 신규 스키마에는 모두 포함되어 있으므로 신규 생성 직후 마이그레이션을 다시 실행하지 않는다.

1. [사건번호·질문 기록](../db/migrations/20261116_case_number_and_question_history.sql)
2. [원문 요약·사고 패턴](../db/migrations/20261116_prosecution_original_summary.sql)
3. [게시판 확장](../db/migrations/20261118_community_features.sql)

기존 사건번호와 사고 패턴은 유지되고, 교체 전에 저장하지 않았던 과거 질문 문장은 복원되지 않는다. 게시판 확장은 기존 글의 작성 체험을 연결된 사건에서 채우며 기존 글과 승인 상태를 보존한다.

- `cases.original_summary`는 1~40자 `text`이고 기존 사건에는 `NULL`을 허용한다.
- `cases`의 사건번호는 `(demo_id,case_number)` 조합으로 고유하다. `case_number_counters`가 체험·연도별 번호를 발급한다.
- `defense_turns.prompts`는 `text[]`, `suggested_answers`는 `jsonb` 배열이다. 이전 질문은 현재 남아 있는 문장부터 기록된다.
- `case_posts.demo_id`는 필수 `uuid`이고 `case_id`는 선택 `uuid`다. 사건 공유는 사건 ID를 보관하고 별도 글쓰기는 `NULL`을 보관한다.
- `post_comments`, `post_likes`, `comment_likes`, `post_bookmarks`를 추가한다. 공감·북마크는 대상 ID와 체험 ID의 복합 기본키로 중복을 막는다. 자료형과 관계는 [현재 전시용 ERD](api-erd.md)를 참조한다.

스키마 소유자가 아닌 FastAPI 전용 DB 계정을 사용하면 새 네 테이블에도 `SELECT`, `INSERT`, `UPDATE`, `DELETE` 권한을 부여한다. `PUBLIC`, `anon`, `authenticated`의 접근 제한은 유지하며 `court_demo`를 Supabase Data API에 노출하지 않는다.

운영 검증에는 실제 Render URL, Vercel Origin, 대상 PostgreSQL 접속 정보, Upstage 키가 필요하다. Upstage 키와 DB 접속 문자열은 서버 환경변수에 설정한다. 아래 예시는 같은 계약을 임시 PostgreSQL과 모의 AI로 실행해 수집한 응답이다.

2026-11-18 검증에서는 임시 PGlite PostgreSQL에 실제 psycopg와 FastAPI TestClient를 연결해 커뮤니티 통합 테스트 9개가 모두 통과했다. 기존 접수·모의 AI 법정 진행·감정·작은 행동·공유·보관함·삭제 및 CORS도 통과했다. 신규 스키마와 v1.3 이관 결과의 동등성, 작성자 채우기, 기존 데이터 보존, 삭제 전파, 공개 역할 권한 제한을 확인했다. 실제 Upstage 호출과 Render·Supabase 운영 배포는 이번 검증에 포함하지 않았다. 재실행 방법은 [README](../README.md#5-검증)를 따른다.

## 8. 실제 요청·응답 예시

### 사건 접수 · 201

헤더에 `X-Demo-Id`와 `Idempotency-Key`를 넣는다. 요청 본문은 `{"thought":"회의에서 말이 꼬였다. 모두 나를 한심하게 봤을 것 같다.","emotion":{"type":"anxiety","intensity":80}}`이다.

```json
{
  "outcome": "case_created",
  "case": {
    "id": "b6779f9d-9613-4b13-9954-017fb0c7866e",
    "case_number": "2026-001",
    "status": "received",
    "version": 0,
    "created_at": "2026-11-16T13:23:14.978000+00:00"
  }
}
```

### 기소 · 200

요청은 `POST /cases/{id}/prosecution`이다. 아래는 모의 AI 응답으로, 실제 Upstage의 후보와 근거는 입력에 따라 달라진다.

```json
{
  "title": "오늘 떠오른 생각 검토 사건",
  "summary": "회의에서 말이 꼬였다. 모두 나를 한심하게 봤을 것 같다.",
  "claim": "회의에서 말이 꼬였다. 모두 나를 한심하게 봤을 것 같다.",
  "pattern_candidates": [],
  "evidence": [],
  "status": "evidence_review",
  "version": 1
}
```

### 변론 첫 질문 · 201

요청은 `POST /cases/{id}/defense/questions`이다.

```json
{
  "id": "4801929f-b994-4353-b266-2ecac186b735",
  "sequence": 1,
  "prompt": "이 생각이 떠오르기 전에 직접 보고 들은 일이 있나요?",
  "revision": 0,
  "prompts": [
    "이 생각이 떠오르기 전에 직접 보고 들은 일이 있나요?"
  ],
  "suggested_answers": [
    {
      "rank": 1,
      "text": "직접 본 일이나 들은 말이 있어요."
    },
    {
      "rank": 2,
      "text": "직접 보거나 들은 내용은 없어요."
    },
    {
      "rank": 3,
      "text": "지금은 정확히 기억나지 않아요."
    }
  ],
  "answer": null,
  "answer_status": "pending",
  "answered_at": null
}
```

### 질문을 쉽게 바꾸기 · 200

요청은 `POST /cases/{id}/defense/questions/{question_id}/simplify`에 `{"revision":0}`을 보낸다. 이후 추천 답변을 제출할 때는 `{"revision":1,"action":"answer","text":"직접 보거나 들은 내용은 없어요."}`처럼 최신 revision을 사용한다.

```json
{
  "id": "4801929f-b994-4353-b266-2ecac186b735",
  "sequence": 1,
  "prompt": "이 생각이 들기 전에 직접 보거나 들은 일이 있나요?",
  "revision": 1,
  "prompts": [
    "이 생각이 떠오르기 전에 직접 보고 들은 일이 있나요?",
    "이 생각이 들기 전에 직접 보거나 들은 일이 있나요?"
  ],
  "suggested_answers": [
    {
      "rank": 1,
      "text": "직접 본 일이나 들은 말이 있어요."
    },
    {
      "rank": 2,
      "text": "직접 보거나 들은 내용은 없어요."
    },
    {
      "rank": 3,
      "text": "지금은 정확히 기억나지 않아요."
    }
  ],
  "answer": null,
  "answer_status": "pending",
  "answered_at": null
}
```

### 세 번째 질문 이후 추가 질문 요청 · 409

```json
{
  "error": {
    "code": "DEFENSE_COMPLETED",
    "message": "세 질문을 모두 처리했습니다. 판결을 요청해 주세요.",
    "request_id": "4eb108c9-b920-4b09-89c7-da15c719d476"
  }
}
```

### 판결 초안 · 200

요청은 `POST /cases/{id}/decision`이다.

```json
{
  "decision": {
    "type": "deferred",
    "reported_facts": [
      "회의에서 말이 꼬였다."
    ],
    "unsupported_claims": [
      "모두 나를 한심하게 봤을 것이다."
    ],
    "suggested_statement": "지금 확인한 내용과 아직 모르는 부분을 나눠 보고, 결론은 보류하겠다.",
    "next_action": null,
    "final_type": null,
    "final_statement": null,
    "finalized_at": null,
    "follow_up_choice": null,
    "follow_up_action": null,
    "follow_up_updated_at": null
  },
  "status": "decision_review",
  "version": 10
}
```

### 판결 문장 재표현 · 200

요청은 `POST /cases/{id}/decision/rephrase`에 `{"style":"shorter","version":10}`을 보낸다.

```json
{
  "suggested_statement": "확인한 것만 살펴보고, 결론은 미루겠다.",
  "status": "decision_review",
  "version": 11
}
```

### 작은 행동 후보 · 200

요청은 종결 후 `GET /cases/{id}/follow-up/options`이다.

```json
{
  "recommended": {
    "title": "가볍게 걷기",
    "description": "편한 곳에서 잠깐 걸어보기"
  },
  "options": [
    {
      "id": "walk",
      "title": "가볍게 걷기",
      "description": "편한 곳에서 잠깐 걸어보기",
      "tag": "5분"
    },
    {
      "id": "music",
      "title": "음악 듣기",
      "description": "좋아하는 음악 한 곡 듣기",
      "tag": "한 곡"
    },
    {
      "id": "water",
      "title": "잠깐 쉬기",
      "description": "물 한 잔 마시고 잠시 쉬기",
      "tag": "2분"
    },
    {
      "id": "note",
      "title": "한 줄 적기",
      "description": "다음에 확인할 일 한 가지 적기",
      "tag": "2분"
    }
  ]
}
```

### 작은 행동 저장 · 200

요청은 `PUT /cases/{id}/follow-up`에 `{"choice":"action","action_text":"편한 곳에서 잠깐 걸어보기"}`를 보낸다.

```json
{
  "choice": "action",
  "action_text": "편한 곳에서 잠깐 걸어보기",
  "follow_up": {
    "choice": "action",
    "action_text": "편한 곳에서 잠깐 걸어보기",
    "updated_at": "2026-11-16T13:23:15.137000+00:00"
  }
}
```

### 종결 사건의 전체 상세 · 200

요청은 `GET /cases/{id}`이다. 휴정 중인 사건도 같은 구조다. 마지막 질문에 답하기 전까지는 `answer_status="pending"`과 `answer=null`이 나타나며, 판결 초안이 없으면 `decision=null`이다.

```json
{
  "case": {
    "id": "b6779f9d-9613-4b13-9954-017fb0c7866e",
    "case_number": "2026-001",
    "status": "closed",
    "original_thought": "회의에서 말이 꼬였다. 모두 나를 한심하게 봤을 것 같다.",
    "summary": "회의에서 말이 꼬였다. 모두 나를 한심하게 봤을 것 같다.",
    "title": "오늘 떠오른 생각 검토 사건",
    "claim": "동료의 평가는 아직 모른다.",
    "version": 14,
    "pattern_candidates": [],
    "pattern_tags": [
      "mind_reading",
      "emotional_reasoning"
    ],
    "primary_pattern": "mind_reading",
    "emotion_before_type": "anxiety",
    "emotion_before_intensity": 80,
    "emotion_after_type": "anxiety",
    "emotion_after_intensity": 50,
    "created_at": "2026-11-16T13:23:14.978000+00:00",
    "updated_at": "2026-11-16T13:23:15.137000+00:00",
    "closed_at": "2026-11-16T13:23:15.123000+00:00",
    "emotion": {
      "type": "anxiety",
      "intensity": 80
    },
    "emotion_after": {
      "type": "anxiety",
      "intensity": 50
    }
  },
  "evidence": [
    {
      "type": "observed",
      "text": "회의에서 말이 꼬였다."
    },
    {
      "type": "interpretation",
      "text": "모두 나를 한심하게 봤을 것이다."
    }
  ],
  "questions": [
    {
      "id": "4801929f-b994-4353-b266-2ecac186b735",
      "sequence": 1,
      "prompt": "이 생각이 들기 전에 직접 보거나 들은 일이 있나요?",
      "revision": 1,
      "prompts": [
        "이 생각이 떠오르기 전에 직접 보고 들은 일이 있나요?",
        "이 생각이 들기 전에 직접 보거나 들은 일이 있나요?"
      ],
      "suggested_answers": [
        {
          "rank": 1,
          "text": "직접 본 일이나 들은 말이 있어요."
        },
        {
          "rank": 2,
          "text": "직접 보거나 들은 내용은 없어요."
        },
        {
          "rank": 3,
          "text": "지금은 정확히 기억나지 않아요."
        }
      ],
      "answer": null,
      "answer_status": "not_sure",
      "answered_at": "2026-11-16T13:23:15.069000+00:00"
    },
    {
      "id": "6792ff67-6571-4b88-bc9d-a2d4fac40de6",
      "sequence": 2,
      "prompt": "지금 이 생각을 판단하기에 어떤 정보가 더 필요할까요?",
      "revision": 0,
      "prompts": [
        "지금 이 생각을 판단하기에 어떤 정보가 더 필요할까요?"
      ],
      "suggested_answers": [
        {
          "rank": 1,
          "text": "직접 확인한 장면을 다시 떠올려 보고 싶어요."
        },
        {
          "rank": 2,
          "text": "다른 가능한 설명이 있는지 살펴보고 싶어요."
        },
        {
          "rank": 3,
          "text": "어떤 정보가 필요한지 아직 모르겠어요."
        }
      ],
      "answer": "다른 사람의 말은 듣지 못했다.",
      "answer_status": "answered",
      "answered_at": "2026-11-16T13:23:15.085000+00:00"
    },
    {
      "id": "a5ff8f67-8d6c-4f47-b5c9-ed2e0d47683e",
      "sequence": 3,
      "prompt": "아직 모르는 부분을 그대로 둔다면 어떤 표현이 상황에 맞을까요?",
      "revision": 0,
      "prompts": [
        "아직 모르는 부분을 그대로 둔다면 어떤 표현이 상황에 맞을까요?"
      ],
      "suggested_answers": [
        {
          "rank": 1,
          "text": "확인한 내용까지만 말할 수 있어요."
        },
        {
          "rank": 2,
          "text": "다른 설명도 가능하다고 생각해요."
        },
        {
          "rank": 3,
          "text": "어떤 표현이 좋을지 아직 모르겠어요."
        }
      ],
      "answer": null,
      "answer_status": "skipped",
      "answered_at": "2026-11-16T13:23:15.097000+00:00"
    }
  ],
  "decision": {
    "type": "deferred",
    "reported_facts": [
      "회의에서 말이 꼬였다."
    ],
    "unsupported_claims": [
      "모두 나를 한심하게 봤을 것이다."
    ],
    "suggested_statement": "확인한 것만 살펴보고, 결론은 미루겠다.",
    "next_action": null,
    "final_type": "deferred",
    "final_statement": "동료의 평가는 알 수 없다.",
    "finalized_at": "2026-11-16T13:23:15.123000+00:00",
    "follow_up_choice": "action",
    "follow_up_action": "편한 곳에서 잠깐 걸어보기",
    "follow_up_updated_at": "2026-11-16T13:23:15.137000+00:00"
  },
  "can_request_decision": false,
  "defense_completed": true
}
```

### 종결 통계 · 200

```json
{
  "closed_count": 1,
  "pattern_counts": {
    "mind_reading": 1,
    "emotional_reasoning": 1
  },
  "emotion_change": {
    "count": 1,
    "average": -30.0
  }
}
```

### 체험 ID 누락·오류 · 422

```json
{
  "error": {
    "code": "INVALID_DEMO_ID",
    "message": "X-Demo-Id에 UUID v4 값을 보내 주세요.",
    "request_id": "5d5769fd-6e49-4b94-ada2-126d92543aa8"
  }
}
```

### 질문 revision 불일치 · 409

```json
{
  "error": {
    "code": "STALE_REVISION",
    "message": "질문 표현이 변경되었습니다. 현재 질문을 다시 확인해 주세요.",
    "request_id": "922801ad-6410-46d8-8122-069ae815a949"
  }
}
```

### AI 응답 오류 · 502

외부 AI 연결 또는 처리 실패에는 같은 형태로 `503 AI_UNAVAILABLE`을 반환한다.

```json
{
  "error": {
    "code": "AI_INVALID_RESPONSE",
    "message": "AI 응답 형식을 확인하지 못했습니다.",
    "request_id": "ec9839cd-7131-499c-819f-71ec74d7920e"
  }
}
```

### 지원 안내 · 200

```json
{
  "outcome": "support_required",
  "support": {
    "message": "지금 이용할 수 있는 도움을 확인해 주세요.",
    "contacts": [
      {
        "label": "자살예방상담전화",
        "number": "109",
        "href": "tel:+82109"
      }
    ]
  }
}
```

### 휴정 후 복원 확인

같은 상세 응답에서 아래처럼 미응답 질문을 확인했다.

```json
{
  "case": {
    "id": "b6779f9d-9613-4b13-9954-017fb0c7866e",
    "status": "defense",
    "version": 6
  },
  "questions": [
    {
      "id": "6792ff67-6571-4b88-bc9d-a2d4fac40de6",
      "sequence": 2,
      "prompt": "지금 이 생각을 판단하기에 어떤 정보가 더 필요할까요?",
      "revision": 0,
      "prompts": [
        "지금 이 생각을 판단하기에 어떤 정보가 더 필요할까요?"
      ],
      "suggested_answers": [
        {
          "rank": 1,
          "text": "직접 확인한 장면을 다시 떠올려 보고 싶어요."
        },
        {
          "rank": 2,
          "text": "다른 가능한 설명이 있는지 살펴보고 싶어요."
        },
        {
          "rank": 3,
          "text": "어떤 정보가 필요한지 아직 모르겠어요."
        }
      ],
      "answer": null,
      "answer_status": "pending",
      "answered_at": null
    }
  ],
  "decision": null,
  "can_request_decision": false,
  "defense_completed": false
}
```

## 9. 프론트엔드 구조와 화면 연결

프론트는 React 19, JavaScript(.js/.jsx), Vite로 구성한다. Tailwind CSS를 우선 사용하고 전역 디자인 토큰은 `src/index.css`에 둔다. React Router에서 도메인별 주소를 정의하고 페이지는 필요한 시점에 불러온다.

| 위치·도구 | 연동에서 맡는 역할 |
|---|---|
| `src/pages/` | 화면 주소의 진입점. 해당 기능을 조합하고 URL의 사건 UUID를 넘긴다. |
| `src/features/` | 접수·변론·판결·보관함·게시판의 화면 구성과 기능별 API 호출을 관리한다. |
| `src/components/`, `src/assets/` | 공용 UI 및 정적 이미지·아이콘을 관리한다. |
| `src/constants/` | 주소와 서버 enum에 대응하는 표시명을 관리한다. |
| `src/routes/` | 도메인별 라우트를 정의한다. `@/`는 `src/`를 가리킨다. |
| TanStack Query | 사건 상세·질문 기록·보관함·게시판·통계 조회와 캐시 처리에 사용한다. |
| Zustand | 필터, 휴정 모달처럼 여러 화면에서 쓰는 UI 상태를 관리한다. |
| Axios 공통 `apiClient` | `VITE_API_BASE_URL`을 기본 주소로 사용하고 `/api/v1` 대상 요청에 `X-Demo-Id`를 붙인다. 기본 주소에 `/api/v1`이 포함되므로 개별 호출은 `/cases`처럼 작성한다. |

### 제공된 화면 주소와 연동할 API

법정 진행 주소는 팀에서 공유한 `feat/court-flow` 작업 정리와 앞서 받은 주소표를 기준으로 한다. API 경로는 공통 `/api/v1`을 생략했다.

| 제공된 화면 주소 | 전시 연동 |
|---|---|
| `/` | 홈. 이어할 사건을 표시하려면 `GET /cases`로 현재 체험의 기록을 조회하고, 선택한 사건의 상세 상태로 이동한다. |
| `/login`, `/signup` | 전시 화면과 메뉴에서 제외한다. 기존 주소로 진입하면 홈으로 replace 이동하며 인증 API를 호출하지 않는다. |
| `/consent` | 팀이 전시에서 안내 화면을 사용하면 동의 상태는 프론트에서 관리한다. 현재 서버에는 동의를 저장하는 API가 없다. |
| `/cases/new` | `POST /cases`로 접수한다. 제출 시 접수 키를 만들고 같은 요청을 재시도할 때 재사용한다. 성공 시 응답의 `case.id`를 다음 화면에 넘긴다. |
| `/cases/:caseId/prosecution` | 기소. 실연동 시 `received`에서는 `POST /cases/{id}/prosecution`을 호출하고, 초안이 저장된 사건은 `GET /cases/{id}`를 사용한다. 근거 확정은 `PUT /cases/{id}/evidence`다. |
| `/cases/:caseId/defense` | 변론. 상세로 질문과 답을 복원한다. 다음 질문은 `POST /cases/{id}/defense/questions`, 답변은 `PUT /cases/{id}/defense/questions/{question_id}/answer`, 쉽게 바꾸기는 `POST /cases/{id}/defense/questions/{question_id}/simplify`다. 질문의 `rank=1`을 강조하고 현재 `revision`을 답변에 포함한다. |
| `/cases/:caseId/decision` | 판결. 변론에서 `POST /cases/{id}/decision`을 호출해 초안을 만든다. 저장된 초안은 상세로 조회하고, 문장을 다시 표현하려면 `POST /cases/{id}/decision/rephrase`를 호출한다. |
| `/cases/:caseId/emotion` | 감정 변화. 접수 감정은 상세의 `case.emotion`에서 읽는다. 다음 버튼에서 `PUT /cases/{id}/decision/final`에 최종 진술문과 선택한 `emotion_after`를 보낸다. |
| `/cases/:caseId/closing` | 종결. 판결 확정 응답의 `case.status=closed`를 표시한다. 새로고침 후에는 상세 조회로 복원한다. |
| `/cases/:caseId/summary` | 사건 요약. 상세의 `case.summary`, `case.primary_pattern`, `decision.final_statement` 및 감정 전후 값을 표시한다. |
| `/cases/:caseId/card` | 사건 카드. 사건 상세에서 선택한 문장으로 브라우저에서 카드를 생성하고 저장·공유한다. |
| `/cases/sample/share` | 기존 주소표의 공유 예시. 실제 사건 공유 화면에서는 선택한 제목·본문·태그 및 공개 범위를 `POST /cases/{id}/posts`에 보낸다. |
| `/cases/:caseId/action` | 행동 제안. 종결된 사건에 `GET /cases/{id}/follow-up/options`를 호출한다. 추천 또는 카드 선택과 쉬기는 `PUT /cases/{id}/follow-up`으로 저장한 뒤 홈으로 이동한다. |
| `/community/shared` | 게시 요청 완료. 요청 결과가 `pending_review`이면 검토 대기로 표시한다. |
| `/community` | 공개 게시판은 `GET /board/posts`, 내가 올린 글은 `GET /board/posts/mine`을 사용한다. 글쓰기 버튼은 사건 없이 `POST /board/posts`를 호출한다. 글 상세·댓글·공감·북마크는 게시판 API를 사용한다. |
| `/court` | 사건 목록은 `GET /cases`, 선택한 사건은 `GET /cases/{id}`, 요약 통계는 `GET /cases/stats`를 사용한다. |
| `/support` | API가 `outcome="support_required"`를 반환하거나 사건 상태가 `support_required`이면 열고 지원 안내를 표시한다. |
| `/dev/recess-modal` | 개발용 휴정 모달 미리보기. 모달을 닫거나 홈으로 돌아가고, 재진입 시 사건 상세로 복원한다. |

실제 사건에서는 `:caseId`에 접수 응답의 `case.id`를 넣는다. `sample` 경로는 프론트 예시 데이터로 처리한다. 팀의 작업 정리에 따르면 일반 목업은 개발 서버에서 `VITE_API_BASE_URL`이 비었을 때 사용한다.

게시글 상세·글쓰기·북마크 화면의 실제 프론트 주소는 공유받은 목록에 없다. `/community/posts/:postId`, `/community/new`, `/community/bookmarks`처럼 도메인 안에서 추가할 수 있으나 이는 권장 주소이며 기존 구현 확인 결과가 아니다. 상세 진입 시 `GET /board/posts/{post_id}`와 `GET /board/posts/{post_id}/comments`를 호출하고, 비밀번호 보호 글은 먼저 `/unlock`으로 받은 본문을 표시한다. 보호 글에는 댓글·공감·북마크 버튼을 표시하지 않는다.

### 접수·사건 조회에서 맞출 필드

- 접수 성공은 HTTP `201`과 `outcome="case_created"`다. `createCase.js`의 목업 응답과 `IntakeForm`·`useCreateCase`의 성공 조건을 함께 맞춘다. 새 사건 ID는 `result.case.id`에서 읽는다. 성공 후 사건 목록 쿼리를 갱신하고 `getProsecutionPath(result.case.id)`로 replace 이동한다.
- 실제 모드의 `getCase(caseId)`는 Axios로 `GET /cases/{caseId}`를 호출한다. 응답 최상위의 `case`에는 `id`, `case_number`, `status`, `summary`, `primary_pattern`, `original_thought`, `emotion`, `emotion_after`가 있다. 접수 감정은 `case.emotion`, 종결 감정은 `case.emotion_after`다. 두 값 모두 `{type,intensity}` 또는 `null`이다. 변론 기록과 판결은 응답 최상위 `questions`와 `decision`에 있다.
- `useCase`를 공용 진입점으로 사용한다. 기존 UI가 평면 사건 객체를 읽는 경우 조회 함수에서 `case` 객체를 펴고, 화면이 필요한 `evidence`, `questions`, `decision`, 진행 플래그를 함께 유지한다. `sample`은 현재 예시 데이터를 반환하고, 일반 목업의 localStorage 조회는 목업 조건에서 사용한다.
- 접수 응답이 HTTP `200`과 `outcome="support_required"`이면 `result.support`를 지원 화면에 전달하고 `/support`로 replace 이동한다. 접수 당시에는 사건 ID가 발급되지 않는다. 휴정 뒤 상세에서 `case.status="support_required"`가 확인된 경우에도 지원 화면으로 이동한다.

### 사건 목록에서 다시 열기

| `status` | 열 화면 |
|---|---|
| `received`, `evidence_review` | `/cases/:caseId/prosecution` |
| `defense` | `/cases/:caseId/defense` |
| `decision_review` | `/cases/:caseId/decision` |
| `closed` | `/cases/:caseId/summary` |
| `support_required` | `/support` |

목업 접수의 위기 판정은 `src/constants/crisisWords.js`의 `includesCrisisWord`를 재사용한다. 실제 요청은 서버의 지원 안내 응답을 따른다. `PageHeader`는 360px과 430px에서 제목과 별 장식이 겹치는지 프론트 화면에서 확인한다.

### 공통 응답 처리

- Axios는 HTTP 200 응답에서도 `outcome="support_required"`를 먼저 확인한다. 지원 안내면 일반 성공 처리 전에 `/support`로 이동한다.
- 사건 상세의 캐시 키에는 체험 ID와 사건 UUID를 모두 넣는다. 접수·답변·판결 확정 이후 관련 쿼리를 갱신하고, 공용 기기에서 새 체험을 시작할 때 이전 화면 상태를 비운다.
- 게시판 쿼리 키에도 체험 ID를 넣는다. 같은 공개 글이라도 `is_mine`, `liked_by_me`, `bookmarked_by_me`가 체험마다 다르기 때문이다. 게시판은 Zustand에 서버 응답을 복사하지 않고 TanStack Query로 조회한다.
- 변론의 `409 DEFENSE_COMPLETED`는 판결 버튼을 여는 신호로 처리한다. 오래된 질문·사건 버전 응답은 상세를 다시 조회해 최신 `revision`과 `case.version`을 반영한다.

| 게시판 작업 | 성공 후 다시 읽을 데이터 |
|---|---|
| 글 작성·수정·삭제 | 공개 목록, 내 글, 해당 상세, 북마크 |
| 글 공감·해제 | 해당 상세와 목록·북마크에 표시된 글의 공감 수·내 공감 상태 |
| 댓글 작성·수정·삭제 | 해당 댓글 목록, 글 상세와 목록의 댓글 수 |
| 댓글 공감·해제 | 해당 댓글 목록 |
| 북마크·해제 | 북마크 목록과 해당 글의 저장 상태 |

공감·북마크 요청 중에는 해당 버튼의 중복 제출을 막고 응답의 상태로 화면을 맞춘다. 댓글·글 작성은 `201` 성공 후 입력을 비운다. `support_required`나 오류에서는 입력을 보존하고 안내를 먼저 처리한다. 삭제 성공인 `204`에서는 JSON 본문을 읽지 않는다. 새 방문자 체험을 시작하면 이전 체험 ID로 만든 쿼리 캐시와 모달·편집 상태를 함께 비운다.

프론트 기술 스택과 화면 주소의 기준은 팀이 공유한 주소표다. 관련 문서 링크는 [read.md](https://app.notion.com/p/read-md-40eb318cd3d6803db8d8e6cfab444a8b?pvs=21)다.

## 10. 게시판 요청·응답 예시

다음은 v1.4 연동 계약을 설명하는 예시다. UUID와 시각은 예시 값이며 실제 호출에서는 서버가 발급한 값을 사용한다. 모든 요청에 같은 `X-Demo-Id`를 보내고, 목록의 `next_cursor=null`이면 마지막 페이지다.

### 별도 글쓰기 · POST /board/posts · 201

요청:

```json
{
  "visibility": "public",
  "title": "작은 실수 때문에 마음이 복잡했던 하루",
  "body": "오늘의 실수 하나로 나 전체를 판단하지 않기로 했어요.",
  "tags": ["mind_reading"]
}
```

응답:

```json
{
  "id": "9c172b65-1e75-48c2-9816-5c3e3f0519bb",
  "status": "pending_review"
}
```

이때 작성 완료 화면은 ‘검토 중’으로 표시한다. 공개 목록에서 바로 찾지 못해도 작성 실패로 처리하지 않는다.

### 내 글 수정 · PUT /board/posts/{post_id} · 200

요청은 `{"title":"오늘 정리한 생각","body":"실수 하나로 내 능력 전체를 판단하지 않으려고 해요.","tags":[]}`다.

```json
{
  "id": "9c172b65-1e75-48c2-9816-5c3e3f0519bb",
  "status": "pending_review"
}
```

### 승인된 공개 글 상세 · GET /board/posts/{post_id} · 200

```json
{
  "id": "9c172b65-1e75-48c2-9816-5c3e3f0519bb",
  "title": "작은 실수 때문에 마음이 복잡했던 하루",
  "body": "오늘의 실수 하나로 나 전체를 판단하지 않기로 했어요.",
  "tags": ["mind_reading"],
  "visibility": "public",
  "status": "published",
  "created_at": "2026-11-18T02:00:00+00:00",
  "published_at": "2026-11-18T02:01:00+00:00",
  "author_label": "익명",
  "is_mine": false,
  "like_count": 1,
  "comment_count": 1,
  "liked_by_me": true,
  "bookmarked_by_me": false
}
```

### 댓글 작성 · POST /board/posts/{post_id}/comments · 201

요청은 `{"body":"저도 비슷한 하루였어요. 이야기해 줘서 고마워요."}`다.

```json
{
  "id": "6a9d8d8e-1f88-457b-9ce1-f0716d7065f0",
  "post_id": "9c172b65-1e75-48c2-9816-5c3e3f0519bb",
  "body": "저도 비슷한 하루였어요. 이야기해 줘서 고마워요.",
  "created_at": "2026-11-18T02:02:00+00:00",
  "updated_at": "2026-11-18T02:02:00+00:00",
  "author_label": "익명",
  "is_mine": true,
  "like_count": 0,
  "liked_by_me": false
}
```

댓글 수정의 요청 형식과 응답 객체도 같다. `created_at`은 유지하고 `updated_at`을 갱신한다. 댓글 조회는 같은 객체를 `{"items":[...],"next_cursor":null}`로 감싼다.

### 글·댓글 공감 · PUT 또는 DELETE /like · 200

공감 `PUT` 응답:

```json
{"liked": true, "like_count": 1}
```

같은 체험의 `PUT`을 반복해도 위 예시의 공감 수는 증가하지 않는다. 다른 체험의 공감이 없을 때 해제 `DELETE` 응답은 다음과 같다.

```json
{"liked": false, "like_count": 0}
```

### 북마크 저장·해제 · PUT 또는 DELETE /bookmark · 200

저장 `PUT` 응답은 `{"bookmarked":true}`, 해제 `DELETE` 응답은 `{"bookmarked":false}`다. `GET /board/bookmarks`는 `{"items":[...],"next_cursor":null}`이며 각 글에 `bookmarked_at`을 추가한다. 정렬·다음 페이지는 `(bookmarked_at DESC,id DESC)` 기준이다.
