"""환경 변수 설정."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # DB: 로컬 개발은 SQLite, 배포는 Supabase PostgreSQL (postgresql://...)
    database_url: str = "sqlite:///./arithon.db"
    # PostgreSQL 에서 사용할 스키마 (명세: court 스키마)
    db_schema: str = "court"
    # 시작 시 테이블 자동 생성 (SQLite/개발용. Supabase 는 schema.sql 적용 권장)
    auto_create_tables: bool = True

    # CORS
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cors_origin_regex: str = r"https://.*\.vercel\.app"

    # AI 공급자: auto | gemini | openai | fallback
    ai_provider: str = "auto"
    gemini_api_key: str | None = None
    google_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    ai_timeout_seconds: float = 40.0

    # 게시판: true 면 새 글을 바로 published 로 저장 (전시 시연용)
    board_auto_publish: bool = False

    # 지원 안내 연락처 (쉼표 구분)
    support_contacts: str = "정신건강 위기상담전화 1577-0199,자살예방 상담전화 109"
    support_message: str = "지금은 재판보다 도움을 받는 것이 먼저예요. 아래 전화로 이야기해 보세요."

    # 요청 제한 (체험 ID 당 분당)
    rate_limit_per_minute: int = 120
    unlock_limit_per_minute: int = 5

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        # Render/Supabase 의 postgres:// 또는 postgresql:// 를 psycopg3 드라이버로 연결
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def support_contact_list(self) -> list[str]:
        return [c.strip() for c in self.support_contacts.split(",") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
