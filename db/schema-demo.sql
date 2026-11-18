-- 체포하겠어 v1.4 전시용 DB | Supabase SQL Editor에서 한 번 실행
-- 별도 court_demo 스키마를 생성한다. 기존 court 및 auth 데이터는 그대로 둔다.
-- 같은 DB에서 다시 실행하면 CREATE SCHEMA에서 중단되므로 재적용할 필요가 없다.
-- Supabase Data API의 노출 스키마 목록에 court_demo를 추가하지 않는다.
BEGIN;

CREATE SCHEMA court_demo;
REVOKE ALL ON SCHEMA court_demo FROM PUBLIC;

CREATE TYPE court_demo.case_status AS ENUM
    ('received', 'evidence_review', 'defense', 'decision_review', 'closed', 'support_required');
CREATE TYPE court_demo.emotion_type AS ENUM
    ('anxiety', 'sadness', 'anger', 'shame', 'guilt', 'other');
CREATE TYPE court_demo.pattern_type AS ENUM
    ('all_or_nothing', 'overgeneralization', 'mind_reading',
     'catastrophizing', 'labeling', 'discounting_positives',
     'emotional_reasoning', 'should_statements', 'personalization', 'blaming', 'comparing');
CREATE TYPE court_demo.evidence_type AS ENUM
    ('observed', 'reported', 'interpretation', 'unknown');
CREATE TYPE court_demo.turn_status AS ENUM ('pending', 'answered', 'skipped', 'not_sure');
CREATE TYPE court_demo.decision_type AS ENUM ('reframed', 'deferred', 'action_needed');
CREATE TYPE court_demo.follow_up_choice AS ENUM ('action', 'rest');
CREATE TYPE court_demo.post_visibility AS ENUM ('public', 'password_protected');
CREATE TYPE court_demo.post_status AS ENUM ('pending_review', 'published', 'hidden');
CREATE TYPE court_demo.report_reason AS ENUM ('personal_information', 'harmful_content', 'other');

CREATE TABLE court_demo.case_number_counters (
    demo_id uuid NOT NULL,
    seoul_year integer NOT NULL,
    last_number bigint NOT NULL CHECK (last_number > 0),
    PRIMARY KEY (demo_id, seoul_year)
);

CREATE TABLE court_demo.cases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    demo_id uuid NOT NULL,
    case_number text NOT NULL,
    CONSTRAINT cases_demo_case_number_key UNIQUE (demo_id, case_number),
    -- 사건 연결 게시글의 체험 ID가 원본 사건 소유자와 일치하도록 참조한다.
    CONSTRAINT cases_id_demo_id_key UNIQUE (id, demo_id),
    status court_demo.case_status NOT NULL DEFAULT 'received',
    original_thought text NOT NULL CHECK (char_length(btrim(original_thought)) BETWEEN 1 AND 2000),
    original_summary text CONSTRAINT cases_original_summary_length
        CHECK (char_length(original_summary) BETWEEN 1 AND 40 AND btrim(original_summary) <> ''),
    title text CHECK (char_length(btrim(title)) BETWEEN 1 AND 120),
    claim text CHECK (char_length(btrim(claim)) BETWEEN 1 AND 2000),
    pattern_candidates court_demo.pattern_type[] NOT NULL DEFAULT '{}',
    pattern_tags court_demo.pattern_type[] NOT NULL DEFAULT '{}',
    emotion_before_type court_demo.emotion_type,
    emotion_before_intensity smallint CHECK (emotion_before_intensity BETWEEN 0 AND 100),
    emotion_after_type court_demo.emotion_type,
    emotion_after_intensity smallint CHECK (emotion_after_intensity BETWEEN 0 AND 100),
    version bigint NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    CHECK ((emotion_before_type IS NULL) = (emotion_before_intensity IS NULL)),
    CHECK ((emotion_after_type IS NULL) = (emotion_after_intensity IS NULL)),
    CHECK ((status = 'closed') = (closed_at IS NOT NULL))
);

-- 카운터 증가는 사건 생성과 같은 트랜잭션에서 처리한다.
-- FastAPI의 DB 역할은 카운터 테이블에 직접 쓸 필요가 없다.
CREATE FUNCTION court_demo.assign_case_number() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    year_number integer := extract(year FROM NEW.created_at AT TIME ZONE 'Asia/Seoul');
    serial_number bigint;
BEGIN
    INSERT INTO court_demo.case_number_counters AS counter (demo_id, seoul_year, last_number)
    VALUES (NEW.demo_id, year_number, 1)
    ON CONFLICT (demo_id, seoul_year) DO UPDATE
    SET last_number = counter.last_number + 1
    RETURNING last_number INTO serial_number;

    NEW.case_number := to_char(NEW.created_at AT TIME ZONE 'Asia/Seoul', 'YYYY') ||
        '-' || lpad(serial_number::text, greatest(3, length(serial_number::text)), '0');
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION court_demo.assign_case_number() FROM PUBLIC;

CREATE TRIGGER cases_assign_number
BEFORE INSERT ON court_demo.cases
FOR EACH ROW
WHEN (NEW.case_number IS NULL)
EXECUTE FUNCTION court_demo.assign_case_number();

CREATE TABLE court_demo.case_evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id uuid NOT NULL REFERENCES court_demo.cases(id) ON DELETE CASCADE,
    type court_demo.evidence_type NOT NULL,
    text text NOT NULL CHECK (char_length(btrim(text)) BETWEEN 1 AND 2000),
    position smallint NOT NULL CHECK (position BETWEEN 1 AND 20),
    UNIQUE (case_id, position)
);

CREATE TABLE court_demo.defense_turns (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id uuid NOT NULL REFERENCES court_demo.cases(id) ON DELETE CASCADE,
    sequence smallint NOT NULL CHECK (sequence BETWEEN 1 AND 3),
    revision smallint NOT NULL DEFAULT 0 CHECK (revision BETWEEN 0 AND 2),
    question text NOT NULL CHECK (char_length(btrim(question)) BETWEEN 1 AND 1000),
    prompts text[] NOT NULL DEFAULT '{}',
    suggested_answers jsonb NOT NULL DEFAULT '[]'::jsonb
        CONSTRAINT defense_suggested_answers_array CHECK (jsonb_typeof(suggested_answers) = 'array'),
    answer text CHECK (char_length(btrim(answer)) BETWEEN 1 AND 1000),
    answer_status court_demo.turn_status NOT NULL DEFAULT 'pending',
    answered_at timestamptz,
    UNIQUE (case_id, sequence),
    CHECK ((answer_status = 'answered') = (answer IS NOT NULL)),
    CHECK ((answer_status = 'pending') = (answered_at IS NULL))
);

CREATE UNIQUE INDEX defense_one_pending_per_case
    ON court_demo.defense_turns (case_id) WHERE answer_status = 'pending';

CREATE TABLE court_demo.case_decisions (
    case_id uuid PRIMARY KEY REFERENCES court_demo.cases(id) ON DELETE CASCADE,
    draft_type court_demo.decision_type NOT NULL,
    reported_facts text[] NOT NULL DEFAULT '{}',
    unsupported_claims text[] NOT NULL DEFAULT '{}',
    suggested_statement text CHECK (char_length(btrim(suggested_statement)) BETWEEN 1 AND 1000),
    next_action text CHECK (char_length(btrim(next_action)) BETWEEN 1 AND 1000),
    final_type court_demo.decision_type,
    final_statement text CHECK (char_length(btrim(final_statement)) BETWEEN 1 AND 1000),
    finalized_at timestamptz,
    follow_up_choice court_demo.follow_up_choice,
    follow_up_action text CHECK (char_length(btrim(follow_up_action)) BETWEEN 1 AND 500),
    follow_up_updated_at timestamptz,
    CHECK ((follow_up_choice IS NULL) = (follow_up_updated_at IS NULL)),
    CHECK ((follow_up_choice IS NOT DISTINCT FROM 'action'::court_demo.follow_up_choice) =
           (follow_up_action IS NOT NULL)),
    CHECK (follow_up_choice IS NULL OR finalized_at IS NOT NULL),
    CHECK ((final_type IS NULL) = (finalized_at IS NULL)),
    CHECK (final_type IS NOT NULL OR final_statement IS NULL),
    CHECK (final_type IS NULL OR final_type = 'deferred' OR final_statement IS NOT NULL)
);

CREATE TABLE court_demo.idempotency_keys (
    demo_id uuid NOT NULL,
    scope text NOT NULL,
    key text NOT NULL CHECK (char_length(key) BETWEEN 1 AND 128),
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    case_id uuid REFERENCES court_demo.cases(id) ON DELETE CASCADE,
    response_status smallint NOT NULL CHECK (response_status IN (200, 201)),
    response_body jsonb NOT NULL CHECK (jsonb_typeof(response_body) = 'object'),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (demo_id, scope, key),
    CHECK ((response_status = 201) = (case_id IS NOT NULL))
);

CREATE TABLE court_demo.case_posts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    demo_id uuid NOT NULL,
    case_id uuid,
    CONSTRAINT case_posts_case_owner_fkey FOREIGN KEY (case_id, demo_id)
        REFERENCES court_demo.cases (id, demo_id) ON DELETE CASCADE,
    visibility court_demo.post_visibility NOT NULL,
    status court_demo.post_status NOT NULL DEFAULT 'pending_review',
    title text NOT NULL CHECK (char_length(btrim(title)) BETWEEN 1 AND 120),
    body text NOT NULL CHECK (char_length(btrim(body)) BETWEEN 1 AND 2000),
    tags text[] NOT NULL DEFAULT '{}' CHECK (cardinality(tags) <= 5 AND array_position(tags, NULL) IS NULL),
    password_hash text,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    CHECK (
        (visibility = 'public' AND password_hash IS NULL) OR
        (visibility = 'password_protected' AND password_hash IS NOT NULL AND
         password_hash LIKE '$argon2id$%')
    ),
    CHECK (status <> 'published' OR published_at IS NOT NULL)
);

-- 운영자는 Supabase Table Editor에서 status를 published 또는 hidden으로 변경한다.
-- 첫 승인 시각은 서버가 설정하며 숨김 후 다시 승인해도 같은 시각을 유지한다.
CREATE FUNCTION court_demo.set_post_published_at() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND OLD.published_at IS NOT NULL THEN
        NEW.published_at := OLD.published_at;
    ELSIF NEW.status = 'published' THEN
        NEW.published_at := now();
    ELSE
        NEW.published_at := NULL;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER case_posts_publish_timestamp
BEFORE INSERT OR UPDATE OF status, published_at ON court_demo.case_posts
FOR EACH ROW EXECUTE FUNCTION court_demo.set_post_published_at();

CREATE TABLE court_demo.post_comments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id uuid NOT NULL REFERENCES court_demo.case_posts(id) ON DELETE CASCADE,
    demo_id uuid NOT NULL,
    body text NOT NULL CHECK (char_length(btrim(body)) BETWEEN 1 AND 1000),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE court_demo.post_likes (
    post_id uuid NOT NULL REFERENCES court_demo.case_posts(id) ON DELETE CASCADE,
    demo_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (post_id, demo_id)
);

CREATE TABLE court_demo.comment_likes (
    comment_id uuid NOT NULL REFERENCES court_demo.post_comments(id) ON DELETE CASCADE,
    demo_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (comment_id, demo_id)
);

CREATE TABLE court_demo.post_bookmarks (
    post_id uuid NOT NULL REFERENCES court_demo.case_posts(id) ON DELETE CASCADE,
    demo_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (post_id, demo_id)
);

CREATE TABLE court_demo.post_reports (
    post_id uuid NOT NULL REFERENCES court_demo.case_posts(id) ON DELETE CASCADE,
    reporter_demo_id uuid NOT NULL,
    reason court_demo.report_reason NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (post_id, reporter_demo_id)
);

CREATE TABLE court_demo.board_unlock_attempts (
    post_id uuid NOT NULL REFERENCES court_demo.case_posts(id) ON DELETE CASCADE,
    bucket text NOT NULL,
    window_started_at timestamptz NOT NULL,
    attempts smallint NOT NULL CHECK (attempts > 0),
    PRIMARY KEY (post_id, bucket)
);

CREATE INDEX cases_demo_created_idx ON court_demo.cases (demo_id, created_at DESC, id DESC);
CREATE INDEX cases_demo_closed_idx ON court_demo.cases (demo_id, closed_at) WHERE status = 'closed';
CREATE INDEX case_posts_case_idx ON court_demo.case_posts (case_id);
CREATE INDEX case_posts_demo_created_idx
    ON court_demo.case_posts (demo_id, created_at DESC, id DESC);
CREATE INDEX case_posts_public_feed_idx ON court_demo.case_posts (published_at DESC, id DESC)
    WHERE visibility = 'public' AND status = 'published';
CREATE INDEX post_comments_post_created_idx
    ON court_demo.post_comments (post_id, created_at, id);
CREATE INDEX post_bookmarks_demo_created_idx
    ON court_demo.post_bookmarks (demo_id, created_at DESC, post_id DESC);
CREATE INDEX board_unlock_attempts_expiry_idx
    ON court_demo.board_unlock_attempts (post_id, window_started_at);
CREATE INDEX idempotency_case_idx ON court_demo.idempotency_keys (case_id) WHERE case_id IS NOT NULL;
CREATE INDEX idempotency_expires_idx ON court_demo.idempotency_keys (expires_at);

REVOKE ALL ON ALL TABLES IN SCHEMA court_demo FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA court_demo FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA court_demo FROM PUBLIC;

-- Supabase의 공개 API 역할도 제한한다. 일반 로컬 PostgreSQL에는 역할이 없다.
DO $$
DECLARE
    api_role text;
BEGIN
    FOREACH api_role IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = api_role) THEN
            EXECUTE format('REVOKE ALL ON SCHEMA court_demo FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA court_demo FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA court_demo FROM %I', api_role);
            EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA court_demo FROM %I', api_role);
        END IF;
    END LOOP;
END;
$$;

COMMIT;
