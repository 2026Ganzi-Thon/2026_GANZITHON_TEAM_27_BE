-- 체포하겠어 v1.0 | Supabase SQL Editor에서 신규 프로젝트에 한 번 실행
BEGIN;

CREATE SCHEMA court;
REVOKE ALL ON SCHEMA court FROM PUBLIC, anon, authenticated;

CREATE TYPE court.case_status AS ENUM
    ('received', 'evidence_review', 'defense', 'decision_review', 'closed', 'support_required');
CREATE TYPE court.emotion_type AS ENUM
    ('anxiety', 'sadness', 'anger', 'shame', 'guilt', 'other');
CREATE TYPE court.pattern_type AS ENUM
    ('all_or_nothing', 'overgeneralization', 'mind_reading',
     'catastrophizing', 'labeling', 'discounting_positives');
CREATE TYPE court.evidence_type AS ENUM
    ('observed', 'reported', 'interpretation', 'unknown');
CREATE TYPE court.turn_status AS ENUM ('pending', 'answered', 'skipped');
CREATE TYPE court.decision_type AS ENUM ('reframed', 'deferred', 'action_needed');

CREATE SEQUENCE court.case_number_seq AS bigint;

CREATE TABLE court.cases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    case_number text NOT NULL UNIQUE DEFAULT (
        to_char(now() AT TIME ZONE 'Asia/Seoul', 'YYYY') || '-' ||
        nextval('court.case_number_seq')::text
    ),
    status court.case_status NOT NULL DEFAULT 'received',
    original_thought text NOT NULL CHECK (char_length(btrim(original_thought)) BETWEEN 1 AND 2000),
    title text CHECK (char_length(btrim(title)) BETWEEN 1 AND 120),
    claim text CHECK (char_length(btrim(claim)) BETWEEN 1 AND 2000),
    pattern_candidates court.pattern_type[] NOT NULL DEFAULT '{}',
    pattern_tags court.pattern_type[] NOT NULL DEFAULT '{}',
    emotion_before_type court.emotion_type,
    emotion_before_intensity smallint CHECK (emotion_before_intensity BETWEEN 0 AND 100),
    emotion_after_type court.emotion_type,
    emotion_after_intensity smallint CHECK (emotion_after_intensity BETWEEN 0 AND 100),
    version bigint NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    CHECK ((emotion_before_type IS NULL) = (emotion_before_intensity IS NULL)),
    CHECK ((emotion_after_type IS NULL) = (emotion_after_intensity IS NULL)),
    CHECK ((status = 'closed') = (closed_at IS NOT NULL))
);

CREATE TABLE court.case_evidence (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id uuid NOT NULL REFERENCES court.cases(id) ON DELETE CASCADE,
    type court.evidence_type NOT NULL,
    text text NOT NULL CHECK (char_length(btrim(text)) BETWEEN 1 AND 2000),
    position smallint NOT NULL CHECK (position BETWEEN 1 AND 20),
    UNIQUE (case_id, position)
);

CREATE TABLE court.defense_turns (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id uuid NOT NULL REFERENCES court.cases(id) ON DELETE CASCADE,
    sequence smallint NOT NULL CHECK (sequence BETWEEN 1 AND 3),
    question text NOT NULL CHECK (char_length(btrim(question)) BETWEEN 1 AND 1000),
    answer text CHECK (char_length(btrim(answer)) BETWEEN 1 AND 1000),
    answer_status court.turn_status NOT NULL DEFAULT 'pending',
    answered_at timestamptz,
    UNIQUE (case_id, sequence),
    CHECK ((answer_status = 'answered') = (answer IS NOT NULL)),
    CHECK ((answer_status = 'pending') = (answered_at IS NULL))
);

CREATE UNIQUE INDEX defense_one_pending_per_case
    ON court.defense_turns (case_id) WHERE answer_status = 'pending';

CREATE TABLE court.case_decisions (
    case_id uuid PRIMARY KEY REFERENCES court.cases(id) ON DELETE CASCADE,
    draft_type court.decision_type NOT NULL,
    reported_facts text[] NOT NULL DEFAULT '{}',
    unsupported_claims text[] NOT NULL DEFAULT '{}',
    suggested_statement text CHECK (char_length(btrim(suggested_statement)) BETWEEN 1 AND 1000),
    next_action text CHECK (char_length(btrim(next_action)) BETWEEN 1 AND 1000),
    final_type court.decision_type,
    final_statement text CHECK (char_length(btrim(final_statement)) BETWEEN 1 AND 1000),
    finalized_at timestamptz,
    CHECK ((final_type IS NULL) = (finalized_at IS NULL)),
    CHECK (final_type IS NOT NULL OR final_statement IS NULL),
    CHECK (final_type IS NULL OR final_type = 'deferred' OR final_statement IS NOT NULL)
);

CREATE TABLE court.idempotency_keys (
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    scope text NOT NULL,
    key text NOT NULL CHECK (char_length(key) BETWEEN 1 AND 128),
    request_hash text NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    case_id uuid REFERENCES court.cases(id) ON DELETE CASCADE,
    response_status smallint NOT NULL CHECK (response_status IN (200, 201)),
    response_body jsonb NOT NULL CHECK (jsonb_typeof(response_body) = 'object'),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, scope, key),
    CHECK ((response_status = 201) = (case_id IS NOT NULL))
);

CREATE INDEX cases_user_created_idx ON court.cases (user_id, created_at DESC, id DESC);
CREATE INDEX cases_user_closed_idx ON court.cases (user_id, closed_at) WHERE status = 'closed';
CREATE INDEX idempotency_case_idx ON court.idempotency_keys (case_id) WHERE case_id IS NOT NULL;
CREATE INDEX idempotency_expires_idx ON court.idempotency_keys (expires_at);

REVOKE ALL ON ALL TABLES IN SCHEMA court FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA court FROM PUBLIC, anon, authenticated;

COMMIT;
