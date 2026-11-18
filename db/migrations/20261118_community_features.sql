-- 기존 v1.3 전시 DB에 API v1.4 배포 전에 한 번 적용한다.
-- 기존 사건 연결 게시글의 소유자, 본문, 공개 범위, 검토 상태를 보존한다.
-- 새 DB는 schema-demo.sql만 실행하고 이 마이그레이션은 실행하지 않는다.
BEGIN;

ALTER TABLE court_demo.cases
    ADD CONSTRAINT cases_id_demo_id_key UNIQUE (id, demo_id);

ALTER TABLE court_demo.case_posts ADD COLUMN demo_id uuid;

UPDATE court_demo.case_posts AS post
SET demo_id = parent.demo_id
FROM court_demo.cases AS parent
WHERE parent.id = post.case_id;

ALTER TABLE court_demo.case_posts
    ALTER COLUMN demo_id SET NOT NULL,
    ALTER COLUMN case_id DROP NOT NULL,
    DROP CONSTRAINT case_posts_case_id_fkey,
    ADD CONSTRAINT case_posts_case_owner_fkey FOREIGN KEY (case_id, demo_id)
        REFERENCES court_demo.cases (id, demo_id) ON DELETE CASCADE;

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

CREATE INDEX case_posts_demo_created_idx
    ON court_demo.case_posts (demo_id, created_at DESC, id DESC);
CREATE INDEX post_comments_post_created_idx
    ON court_demo.post_comments (post_id, created_at, id);
CREATE INDEX post_bookmarks_demo_created_idx
    ON court_demo.post_bookmarks (demo_id, created_at DESC, post_id DESC);

REVOKE ALL ON ALL TABLES IN SCHEMA court_demo FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA court_demo FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA court_demo FROM PUBLIC;

-- Supabase Data API를 통해 체험 데이터를 직접 읽거나 쓰지 못하도록 유지한다.
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
