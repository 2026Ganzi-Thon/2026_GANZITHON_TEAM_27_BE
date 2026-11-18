# 체포하겠어 · 전시용 백엔드 v1.4

최신 연동 기준은 [전시용 API 명세서 v1.4](docs/api-spec.md)다. [ERD](docs/api-erd.md), [OpenAPI](docs/openapi.json), [프론트 연동 가이드](docs/frontend-integration.md)를 함께 제공한다.

FastAPI가 익명 체험 ID별 사건과 게시판을 PostgreSQL에 저장한다. LangGraph가 기소, 변론 질문, 질문 재표현, 판결 초안, 판결 문장 재표현을 생성한다. v1.4는 기존 사건 흐름에 별도 글쓰기, 댓글, 글·댓글 공감, 북마크를 추가한다. 회원가입·로그인·토큰 검증 없이 `X-Demo-Id`를 사용한다. [v1.2 요구사항](docs/requirements-v1.2-demo.md)은 이전 기본 요구사항 보관본이다.

## v1.4 브랜치 안내

이 브랜치는 별도로 제작한 v1.4 백엔드를 보관한다. 기존 `main`의 SQLAlchemy·SQLite·`court` 구현을 비동기 psycopg·PostgreSQL·`court_demo` 구현으로 교체한 비교용 브랜치다.

- Python 3.12와 `AI_PROVIDER=mock` 또는 `upstage`를 사용한다.
- `db/migrations/`는 기존 `court_demo` 스키마용이며, `main`의 `court` 데이터를 변환하는 이관 SQL이 아니다.
- 현재 프론트 `main`(`a7778de`)에는 목업 호출과 다른 요청·응답 형식이 남아 있다. 실제 연결에는 프론트 통신 전환 및 API 매핑 조정이 필요하다. 이 업로드에는 그 연동 문제의 수정이 포함되지 않는다.
- 아래 연동 문서는 v1.4가 제공하는 계약을 설명한다. 프론트의 현재 구현 완료 상태를 뜻하지 않는다.

## 1. 실행 환경

Python 3.12에서 프로젝트 폴더를 열고 다음을 실행한다.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
```

`.env`의 `DATABASE_URL`을 아래 방식 중 하나로 설정하고 서버를 실행한다.

```bash
python -m uvicorn app.main:app --reload
```

| 주소 | 기능 |
|---|---|
| `http://127.0.0.1:8000/docs` | Swagger에서 API 호출 |
| `http://127.0.0.1:8000/health/live` | 서버 실행 확인 |
| `http://127.0.0.1:8000/health` | PostgreSQL 연결 확인 |

## 2. PostgreSQL 연결

### Supabase 개발 프로젝트

1. SQL Editor에서 [db/schema-demo.sql](db/schema-demo.sql)을 한 번 실행한다. `court_demo` 스키마가 생성되며 기존 `court` 데이터와 회원 테이블을 보존한다.
2. 전시용 FastAPI DB 계정에 `court_demo` 스키마 사용 권한과 업무 테이블의 조회·쓰기 권한을 부여한다. `court_demo`는 Supabase Data API에 노출하지 않는다.
3. Supabase **Connect**에서 PostgreSQL 연결 문자열을 복사한다. IPv4 연결에는 Session pooler를 사용할 수 있다. TLS를 켜고 `DATABASE_URL`에 입력한다. [Supabase PostgreSQL 연결 안내](https://supabase.com/docs/guides/database/connecting-to-postgres)
4. FastAPI를 다시 시작한다. `/health`가 200이면 DB 접속에 성공했다. 실제 스키마 적용 여부는 아래 사건 조회 또는 접수로 확인한다.

### Docker로 로컬 PostgreSQL 실행

```bash
docker compose up -d --wait postgres
```

`.env`에 로컬 DB 주소를 넣는다.

```dotenv
DATABASE_URL=postgresql://court:local-only-password@127.0.0.1:54322/court
AI_PROVIDER=mock
```

새 로컬 볼륨을 처음 시작할 때 전시용 SQL이 적용된다. 기존 v1.3 전시 DB는 [게시판 확장 마이그레이션](db/migrations/20261118_community_features.sql)을 API 배포 전에 실행한다. 더 이전 버전은 [v1.4 명세서의 적용 순서](docs/api-spec.md#7-db-적용검증)에 따라 빠진 것만 실행한다. 스키마 소유자가 아닌 FastAPI 전용 DB 계정에는 새 네 테이블의 `SELECT`, `INSERT`, `UPDATE`, `DELETE` 권한도 부여한다. 기존 회원 기반 DB 스키마는 `db/schema.sql`에 보관되어 있다.

## 3. 모바일과 Swagger에서 요청 보내기

모바일 앱은 처음 실행할 때 무작위 UUID v4를 생성해 기기에 저장하고 모든 `/api/v1` 요청에 `X-Demo-Id`를 보낸다. 같은 ID로 만든 사건은 같은 보관함에 표시된다. 공용 전시 기기에서 새 방문자의 체험을 시작할 때는 새 UUID를 생성한다.

Swagger에서 `POST /api/v1/cases`를 실행할 때 체험 ID와 접수마다 새로운 `Idempotency-Key`를 입력한다. 같은 접수의 재시도에는 같은 키를 쓴다. 다음은 가상 데이터 예시다.

```bash
curl -sS http://127.0.0.1:8000/api/v1/cases \
  -H 'Content-Type: application/json' \
  -H 'X-Demo-Id: 31963b07-b1c1-4397-a847-ffed50f572d7' \
  -H 'Idempotency-Key: exhibition-demo-1' \
  -d '{"thought":"회의에서 말이 꼬였어. 모두 나를 한심하게 봤을 것 같아.","emotion":{"type":"anxiety","intensity":80}}'
```

반환된 `case.id`와 같은 체험 ID로 기소, 근거 확인, 변론, 판결을 이어간다. 상세 조회 응답에는 사건, 근거, 질문, 판결이 담겨 있다. 앱은 이 응답으로 마지막 저장 상태를 복원한다. 미제출 입력 보관과 PNG 사건 카드 내보내기는 모바일 앱에서 처리한다.

게시판 글은 종결 사건 공유 또는 사건 없는 별도 글쓰기로 작성한다. 글 작성과 수정 후에는 `pending_review`다. 승인하려면 운영자가 Supabase에서 `court_demo.case_posts`의 해당 글 상태를 `published`로 변경한다. 승인된 공개 글에서 댓글·공감·북마크를 사용한다. 비밀번호 보호 글은 목록에서 제외되며, 열람자는 글 ID와 비밀번호를 `/board/posts/{post_id}/unlock`에 보낸다.

## 4. LangGraph와 Render 설정

기본 AI 모드는 `mock`이다. Upstage Solar를 연결하려면 프로젝트의 `.env`에 아래 항목을 설정하고 서버를 다시 시작한다. `AI_MODEL`을 생략하면 `solar-mini4`를 사용한다. [Upstage Chat API](https://console.upstage.ai/ko/api/chat)

```dotenv
AI_PROVIDER=upstage
AI_MODEL=solar-mini4
UPSTAGE_API_KEY=발급받은_키
```

DB가 연결된 Swagger에서 가상 사건을 접수하고 해당 사건의 기소 API를 호출하면 모델 연동을 확인할 수 있다. `python -m app.smoke`는 API 키가 필요 없는 모의 실행이다. 위기 안내 분기는 전시용 표현 규칙으로 구현되어 있어 실서비스에 사용하기 전 검증이 필요하다.

Render는 [render.yaml](render.yaml) 설정을 사용한다. 배포에서 Upstage를 사용하려면 Render의 서버 환경변수에 `DATABASE_URL`, `AI_PROVIDER=upstage`, `AI_MODEL`, `UPSTAGE_API_KEY`를 설정한다. 프론트에는 FastAPI 주소를 지정한다. Vercel 미리보기 또는 배포 도메인은 Render의 `CORS_ORIGINS`에 정확한 주소를 콤마로 구분해 등록한다. 비밀번호와 API 키는 서버 설정에 보관한다. [Render FastAPI 배포 문서](https://render.com/docs/deploy-fastapi)

## 5. 검증

전용 테스트 PostgreSQL에 `db/schema-demo.sql`을 적용한 뒤 다음을 실행한다. 운영 DB 접속 정보를 사용하지 않는다.

```bash
TEST_DATABASE_URL='postgresql://<테스트접속정보>' python -m unittest discover -s tests -v
```

`TEST_DATABASE_URL`이 없으면 DB 통합 테스트는 건너뛴다. 2026-11-18에는 임시 PGlite PostgreSQL에 실제 psycopg와 FastAPI TestClient를 연결해 커뮤니티 통합 테스트 9개가 모두 통과했다. 별도 글쓰기, 댓글 소유권, 공감·북마크 중복 방지, 보호·미승인 글 접근 제한, 삭제 전파와 기존 사건 공유를 확인했다.

기존 접수→모의 AI 법정 진행→감정→작은 행동→공유→보관함→삭제 및 CORS 검증도 통과했다. 신규 스키마와 기존 v1.3 DB 이관의 동등성, 작성자 채우기, 기존 기록 보존, 삭제 전파, 공개 역할 권한 제한을 검증했다. 실제 Upstage 호출과 Render·Supabase 운영 배포는 이번 로컬 검증에 포함하지 않았다.
