-- 기존 전시 DB 업데이트. API 배포 전에 Supabase SQL Editor에서 한 번 실행한다.
-- 신규 DB는 이 파일 없이 db/schema-demo.sql을 적용한다.
-- 기존 사건번호를 보존하고, 현재 저장된 번호의 연도·일련번호를 기준으로 다음 번호를 발급한다.
BEGIN;

LOCK TABLE court_demo.cases, court_demo.defense_turns IN ACCESS EXCLUSIVE MODE;

CREATE TABLE court_demo.case_number_counters (
    demo_id uuid NOT NULL,
    seoul_year integer NOT NULL,
    last_number bigint NOT NULL CHECK (last_number > 0),
    PRIMARY KEY (demo_id, seoul_year)
);

REVOKE ALL ON TABLE court_demo.case_number_counters FROM PUBLIC;

INSERT INTO court_demo.case_number_counters (demo_id, seoul_year, last_number)
SELECT demo_id, left(case_number, 4)::integer, max(substring(case_number FROM 6)::bigint)
FROM court_demo.cases
WHERE case_number ~ '^[0-9]{4}-[0-9]+$'
GROUP BY demo_id, left(case_number, 4)
HAVING max(substring(case_number FROM 6)::bigint) > 0;

ALTER TABLE court_demo.cases
    ALTER COLUMN case_number DROP DEFAULT,
    DROP CONSTRAINT cases_case_number_key,
    ADD CONSTRAINT cases_demo_case_number_key UNIQUE (demo_id, case_number);

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

DROP SEQUENCE court_demo.case_number_seq;

ALTER TABLE court_demo.defense_turns
    ADD COLUMN prompts text[] NOT NULL DEFAULT '{}',
    ADD COLUMN suggested_answers jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD CONSTRAINT defense_suggested_answers_array CHECK (jsonb_typeof(suggested_answers) = 'array');

UPDATE court_demo.defense_turns SET prompts = ARRAY[question];

COMMIT;
