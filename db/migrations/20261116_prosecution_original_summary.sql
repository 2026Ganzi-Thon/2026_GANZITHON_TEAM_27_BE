-- 기존 전시 DB 업데이트. 기존 사건의 원문과 패턴은 유지한다.
-- 이전 마이그레이션을 적용한 DB에 API 배포 전에 한 번 실행한다.
BEGIN;

ALTER TYPE court_demo.pattern_type ADD VALUE IF NOT EXISTS 'emotional_reasoning';
ALTER TYPE court_demo.pattern_type ADD VALUE IF NOT EXISTS 'should_statements';
ALTER TYPE court_demo.pattern_type ADD VALUE IF NOT EXISTS 'personalization';
ALTER TYPE court_demo.pattern_type ADD VALUE IF NOT EXISTS 'blaming';
ALTER TYPE court_demo.pattern_type ADD VALUE IF NOT EXISTS 'comparing';

ALTER TABLE court_demo.cases
    ADD COLUMN original_summary text CONSTRAINT cases_original_summary_length
        CHECK (char_length(original_summary) BETWEEN 1 AND 40 AND btrim(original_summary) <> '');

COMMIT;
