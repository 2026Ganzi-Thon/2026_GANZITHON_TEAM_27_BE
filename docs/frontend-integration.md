# 체포하겠어 · 전시용 v1.4 프론트엔드 연동 가이드

2026-11-18 기준. 전체 계약과 JSON 예시는 [API 명세서 v1.4](api-spec.md), 자료형과 관계는 [ERD](api-erd.md), 요청 스키마는 [OpenAPI](openapi.json)를 참조한다. 이 문서는 프론트에 반영할 연결 규칙이며 프론트 저장소의 구현 완료를 뜻하지 않는다.

## 프로젝트 기준

React 19, JavaScript(.js/.jsx), Vite, Tailwind CSS를 사용한다. 전역 토큰은 `src/index.css`, 화면 진입점은 `src/pages`, 기능별 UI·요청은 `src/features`, 공용 UI는 `src/components`에 둔다. `src/routes`에서 React Router 주소를 연결하고 페이지는 lazy loading한다. `@/`는 `src/`다.

서버 데이터는 TanStack Query, 공통 요청은 Axios `apiClient`를 사용한다. Zustand에는 필터·모달 등 공유 UI 상태를 둔다. 서버 응답을 별도 전역 저장소에 복사하지 않는다.

## 공통 연결

Vite 설정:

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000/api/v1
```

Axios 기본 주소에 `/api/v1`이 포함되므로 개별 호출은 `/cases`, `/board/posts`처럼 쓴다. 앱이 생성·보관한 UUID v4를 모든 업무 요청의 `X-Demo-Id`에 붙인다. `Authorization`과 회원가입·로그인 API는 사용하지 않는다. `/login`, `/signup` 화면과 메뉴는 전시 흐름에서 제외하고 기존 주소 진입은 홈으로 replace 이동한다. 동의 안내의 완료 상태는 기기에서 관리한다.

브라우저 로컬 주소 `http://localhost:5173`, `http://127.0.0.1:5173`은 기본 허용한다. 배포 프론트 주소는 FastAPI의 `CORS_ORIGINS`에 정확한 Origin으로 등록한다. 프론트에는 Upstage 키나 PostgreSQL 연결 문자열을 넣지 않는다.

## 사건 진행 연결

| 화면·동작 | 호출·필드 |
|---|---|
| 접수 | `POST /cases` + 접수별 `Idempotency-Key`. 성공은 `201`, `outcome="case_created"`, 다음 화면 ID는 `result.case.id` |
| 기소 | `POST /cases/{id}/prosecution`. 저장된 사건은 `GET /cases/{id}`로 복원 |
| 근거 확인 | `PUT /cases/{id}/evidence`에 claim·태그·수정 근거 |
| 변론 | `POST /cases/{id}/defense/questions`. 최신 revision으로 답변 PUT. 최대 3문항, 재표현은 문항당 최대 2회 |
| 도움 버튼 | answer의 `action="not_sure"` 또는 `"skip"`, 쉽게 바꾸기는 `/simplify` |
| 판결 | `POST /cases/{id}/decision`, 문장 재표현은 `/decision/rephrase`에 최신 버전 |
| 감정 변화 | `PUT /cases/{id}/decision/final`에 유형·최종 문장·선택 감정. 이때 종결 |
| 종결·요약 | `case.summary`, `case.primary_pattern`, `decision.final_statement`, `case.emotion`, `case.emotion_after` |
| 작은 행동 | 종결 후 `GET /cases/{id}/follow-up/options`, 선택·쉬기는 `PUT /cases/{id}/follow-up` |
| 사건 카드 | 상세에서 선택한 문장으로 프론트에서 PNG 생성·저장·공유 |

상세는 `{case,evidence,questions,decision,can_request_decision,defense_completed}` 구조다. `summary`는 원문 40자 이내 요약이며 기존 사건에 없으면 `original_thought`를 표시한다. 대표 패턴은 `primary_pattern`, 감정 전후는 `{type,intensity}` 또는 null이다. 사고 패턴 표시명은 서버 11종을 모두 매핑한다.

질문의 `prompts`에는 최초부터 최신 문장까지 남고 `suggested_answers`는 `{rank,text}` 3개다. 추천을 누르면 text를 `action="answer"`로 보낸다. `can_request_decision`으로 판결 버튼을 제어한다. 세 질문 완료 후의 `409 DEFENSE_COMPLETED`도 판결로 넘어가는 신호다.

실제 사건 주소는 `/cases/:caseId/prosecution → defense → decision → emotion → closing → summary → action` 순서다. `sample`은 예시 데이터로 처리하고 실제 모드의 `getCase`는 서버를 조회한다. 저장 상태별 재진입 주소는 명세서의 화면 연결 표를 따른다. 미제출 편집은 체험·사건·단계별로 임시 보관한다.

## 게시판 연결

| 화면·동작 | API |
|---|---|
| 공개 목록·검색 | `GET /board/posts?tag=...&q=...&limit=20&cursor=...` |
| 내가 작성한 글 | `GET /board/posts/mine` |
| 사건 공유 | `POST /cases/{id}/posts` — 내 종결 사건 필요 |
| 별도 글쓰기 | `POST /board/posts` — 사건 ID 불필요 |
| 글 상세 | `GET /board/posts/{post_id}` |
| 내 글 수정·삭제 | `PUT`·`DELETE /board/posts/{post_id}` |
| 보호 글 열기 | `POST /board/posts/{post_id}/unlock`에 `{password}` |
| 댓글 조회·작성 | `GET`·`POST /board/posts/{post_id}/comments` |
| 내 댓글 수정·삭제 | `PUT`·`DELETE /board/comments/{comment_id}` |
| 글 공감·해제 | `PUT`·`DELETE /board/posts/{post_id}/like` |
| 댓글 공감·해제 | `PUT`·`DELETE /board/comments/{comment_id}/like` |
| 북마크·해제 | `PUT`·`DELETE /board/posts/{post_id}/bookmark` |
| 저장한 글 목록 | `GET /board/bookmarks` |
| 글 신고 | `POST /board/posts/{post_id}/reports` |

글 작성은 `{visibility,title,body,tags?,password?}`다. 제목 1~120자, 본문 1~2,000자, 태그 최대 5개(각 1~24자)다. 공개 글에서는 password를 생략하고 보호 글만 8~64자 비밀번호를 보낸다. 응답은 `{id,status:"pending_review"}`이므로 완료 화면은 검토 중으로 표시한다. 운영자 승인 후 공개 목록에서 볼 수 있다. 내 글 수정은 title·body가 필수이며 tags 생략·null은 유지, `[]`는 모두 제거다. 수정하면 다시 검토 중이 된다.

글 목록·상세·내 글·잠금 해제는 같은 게시글 객체다. 기존 `id,title,body,tags,published_at` 외에 `visibility,status,created_at,author_label,is_mine,like_count,comment_count,liked_by_me,bookmarked_by_me`를 읽는다. 작성자 표시는 `author_label`, 수정·삭제 메뉴는 `is_mine`을 쓴다. 응답에 체험 ID나 원본 사건 ID는 없다.

댓글 작성·수정은 `{body}`이며 1~1,000자다. 댓글 객체는 `id,post_id,body,created_at,updated_at,author_label,is_mine,like_count,liked_by_me`다. 댓글 목록은 오래된 순서이고 새 댓글은 마지막에 붙는다. 상대 시각은 API 시각에서 계산한다.

공감·북마크는 `PUT`으로 켜고 `DELETE`로 끈다. 재전송해도 중복으로 세지 않는다. 공감 응답은 `{liked,like_count}`, 북마크는 `{bookmarked}`다. 북마크 목록은 저장 최신순이며 `bookmarked_at`이 저장 시각이다. 숨김·검토 중인 글은 저장 목록에서도 잠시 제외된다.

댓글·공감·북마크 버튼은 `visibility="public" && status="published"`인 글에만 표시한다. 보호 글을 비밀번호로 열었더라도 이 기능은 사용하지 않는다. 보호 글의 공개 목록 노출·인기순·대댓글·댓글 신고 API는 제공하지 않는다.

게시글 상세·작성·북마크의 확정 프론트 주소는 아직 공유되지 않았다. `/community/posts/:postId`, `/community/new`, `/community/bookmarks`는 권장 주소이며 현재 구현된 주소라는 뜻은 아니다.

## 조회 캐시와 완료 처리

모든 개인화 쿼리 키에 체험 ID를 포함한다. 공개 글도 내 공감·저장 상태가 달라 같은 원칙을 적용한다. 다음처럼 구성할 수 있다.

```js
['board', demoId, 'posts', { tag, q }]
['board', demoId, 'post', postId]
['board', demoId, 'comments', postId]
['board', demoId, 'mine']
['board', demoId, 'bookmarks']
```

목록별 `next_cursor`는 같은 필터의 다음 페이지에만 사용한다. 공개 목록은 승인 시각 최신순, 내 글은 작성 최신순, 댓글은 작성 오래된 순, 북마크는 저장 최신순이다. 기본 20개, 최대 50개다.

| 변경 | 다시 읽을 쿼리 |
|---|---|
| 글 작성·수정·삭제 | 공개 목록·내 글·해당 상세·북마크 |
| 글 공감·해제 | 글 상세와 목록의 공감 수·내 상태 |
| 댓글 작성·수정·삭제 | 댓글 목록과 글 상세·목록의 댓글 수 |
| 댓글 공감·해제 | 댓글 목록 |
| 북마크·해제 | 북마크 목록과 글의 저장 상태 |

요청 중 해당 버튼의 중복 제출을 막고 성공 응답으로 표시를 확정한다. `204`는 본문을 읽지 않는다. 새 방문자 체험을 시작하면 이전 체험의 Query 캐시와 UI·미제출 입력을 지운 뒤 새 ID로 요청한다.

## 지원 안내와 오류

Axios는 HTTP `200`에서도 `outcome="support_required"`를 일반 성공보다 먼저 확인한다. 지원 안내면 `/support`로 이동하고 `support.contacts`로 화면을 그린다. 사건 흐름·작은 행동 외에 글·댓글 작성과 수정에서도 이 응답이 올 수 있다. 해당 입력은 저장되지 않으므로 일반 성공처럼 입력을 지우거나 완료 화면으로 이동하지 않는다.

`404 POST_NOT_FOUND`는 글 없음·접근 조건 불충족, `COMMENT_NOT_FOUND`는 댓글 없음·접근 불가·수정 소유권 불일치다. `409`의 사건 상태·버전·질문 충돌은 상세를 다시 조회한다. `422`는 입력·체험 ID·커서 형식을 확인한다. 보호 글 재입력 제한은 `429 UNLOCK_RATE_LIMITED`다. AI·DB 장애인 `502`·`503`에서는 입력을 보존하고 재시도한다.
