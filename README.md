# 생각법정 백엔드 (FastAPI · 전시용 v1.1)

노션 `BE 개발환경 › 전시용 api명세서` 기준 구현. 로그인 없이 `X-Demo-Id`(UUID v4) 헤더로 체험자를 구분한다.
API 계약 상세는 [docs/API.md](docs/API.md), Swagger 는 실행 후 `http://localhost:8080/docs`.

| 항목 | 내용 |
| --- | --- |
| 프레임워크 | FastAPI (Python 3.10+) |
| DB | Supabase PostgreSQL (`court` 스키마, [schema.sql](schema.sql)) / 로컬은 SQLite |
| AI | LangGraph 요청 단위 그래프 (기소·변론 질문·질문 재표현·판결·판결 재표현) + Pydantic 검증. Gemini 또는 OpenAI, 키가 없으면 규칙 기반 생성기 |
| 비밀번호 | Argon2id |
| 배포 | Render Web Service ([render.yaml](render.yaml)) |

## 로컬 실행

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # 필요하면 GEMINI_API_KEY 등 입력
.\.venv\Scripts\uvicorn app.main:app --reload --port 8080
```

프론트(`frontend/.env.local`): `VITE_API_BASE_URL=http://localhost:8080/api/v1` 후 `npm run dev`.

테스트: `.\.venv\Scripts\python -m pytest -q`

## Supabase · Render 배포

1. Supabase SQL Editor 에서 `schema.sql` 실행.
2. Render 에 이 저장소 연결(`render.yaml`) → 환경변수 `DATABASE_URL`(Supabase connection string), `GEMINI_API_KEY`(선택), `CORS_ORIGINS`(배포 프론트 주소) 입력.
3. 프론트 Vercel 환경변수 `VITE_API_BASE_URL=https://<render-service>.onrender.com/api/v1`.
4. 게시글 승인: Supabase Table Editor 에서 `court.case_posts.status` 를 `published` 로 변경(트리거가 `published_at` 기록). 시연 중 바로 노출하려면 `BOARD_AUTO_PUBLISH=true`.

## 명세와 다르게/추가로 정한 점

- `case_number`: 프론트 요청대로 체험 ID 별 `연도-001` (고유 제약은 `(demo_id, case_number)`).
- 게시글 비밀번호: 명세 8~64자 → 프론트 화면 규칙에 맞춰 6~64자 허용.
- `GET /cases/{id}` 에 프론트가 요청한 필드(`summary`, `defense[].prompts`, `suggested_answers`, `decision.version`, `follow_up`, `open_question`)를 포함.
- 변론 질문 응답에 추천 답변 3개(`suggested_answers`) 포함. 3개 처리 후 `409 DEFENSE_COMPLETED`.
- 이미 지난 단계의 `POST /prosecution`, `POST /decision` 재요청은 저장된 결과를 돌려준다.
- 확장(명세 외, 프론트 화면용): `POST /board/posts`(사건 없이 글쓰기), `POST /board/posts/{id}/like`, `POST /board/posts/{id}/comments`.
- 지원 안내 판단은 키워드 기반(`app/safety.py`). 안내로 전환된 원문은 저장하지 않는다.
- 요청 제한은 프로세스 메모리 기반(체험 ID 당 분당 120회, unlock 은 글당 분당 5회).
