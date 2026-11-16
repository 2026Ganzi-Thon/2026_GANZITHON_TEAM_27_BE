-- 생각법정 전시용 v1.1 스키마 (Supabase PostgreSQL)
-- Supabase SQL Editor 에서 실행한다. 서버는 search_path=court,public 으로 연결한다.
-- (AUTO_CREATE_TABLES=true 로 두면 서버가 같은 구조를 자동 생성하지만, 운영 DB 는 이 파일 적용을 권장)

create schema if not exists court;
set search_path = court, public;

create table if not exists cases (
    id uuid primary key default gen_random_uuid(),
    demo_id uuid not null,
    case_number varchar(32) not null,
    status varchar(32) not null default 'received'
        check (status in ('received','evidence_review','defense','decision_review','closed','support_required')),
    original_thought text not null,
    title text,
    claim text,
    summary text,
    pattern_candidates json not null default '[]',
    pattern_tags json not null default '[]',
    emotion_before_type varchar(16) check (emotion_before_type in ('anxiety','sadness','anger','shame','guilt','other')),
    emotion_before_intensity smallint check (emotion_before_intensity between 0 and 100),
    emotion_after_type varchar(16) check (emotion_after_type in ('anxiety','sadness','anger','shame','guilt','other')),
    emotion_after_intensity smallint check (emotion_after_intensity between 0 and 100),
    version bigint not null default 1,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    closed_at timestamptz,
    constraint uq_cases_demo_case_number unique (demo_id, case_number),
    constraint ck_cases_emotion_before_pair check ((emotion_before_type is null) = (emotion_before_intensity is null)),
    constraint ck_cases_emotion_after_pair check ((emotion_after_type is null) = (emotion_after_intensity is null))
);
create index if not exists ix_cases_demo_created on cases (demo_id, created_at desc, id desc);
create index if not exists ix_cases_demo_closed on cases (demo_id, closed_at);

create table if not exists case_evidence (
    id uuid primary key default gen_random_uuid(),
    case_id uuid not null references cases(id) on delete cascade,
    type varchar(16) not null check (type in ('observed','reported','interpretation','unknown')),
    text text not null,
    position smallint not null,
    constraint uq_case_evidence_position unique (case_id, position)
);

create table if not exists defense_turns (
    id uuid primary key default gen_random_uuid(),
    case_id uuid not null references cases(id) on delete cascade,
    sequence smallint not null check (sequence between 1 and 3),
    question text not null,
    prompts json not null default '[]',
    suggested_answers json not null default '[]',
    answer text,
    answer_status varchar(16) not null default 'pending'
        check (answer_status in ('pending','answered','skipped','not_sure')),
    revision smallint not null default 0 check (revision between 0 and 2),
    created_at timestamptz not null default now(),
    answered_at timestamptz,
    constraint uq_defense_turns_sequence unique (case_id, sequence),
    constraint ck_defense_turns_answer_text check ((answer_status = 'answered') = (answer is not null))
);
-- 사건당 미응답 질문 최대 1개
create unique index if not exists uq_defense_turns_one_pending on defense_turns (case_id) where answer_status = 'pending';

create table if not exists case_decisions (
    case_id uuid primary key references cases(id) on delete cascade,
    draft_type varchar(16) not null check (draft_type in ('reframed','deferred','action_needed')),
    reported_facts json not null default '[]',
    unsupported_claims json not null default '[]',
    suggested_statement text not null,
    next_action text,
    final_type varchar(16) check (final_type in ('reframed','deferred','action_needed')),
    final_statement text,
    finalized_at timestamptz,
    follow_up_choice varchar(16) check (follow_up_choice in ('action','rest')),
    follow_up_action text,
    follow_up_updated_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint ck_case_decisions_follow_up_action
        check (follow_up_choice is null or follow_up_choice <> 'action' or follow_up_action is not null)
);

create table if not exists idempotency_keys (
    demo_id uuid not null,
    scope varchar(64) not null,
    key varchar(255) not null,
    request_hash varchar(128) not null,
    case_id uuid references cases(id) on delete cascade,
    response_status smallint not null,
    response_body json not null,
    expires_at timestamptz not null,
    primary key (demo_id, scope, key)
);

create table if not exists case_posts (
    id uuid primary key default gen_random_uuid(),
    demo_id uuid not null,
    case_id uuid references cases(id) on delete cascade,
    visibility varchar(32) not null check (visibility in ('public','password_protected')),
    status varchar(32) not null default 'pending_review' check (status in ('pending_review','published','hidden')),
    title text not null,
    body text not null,
    tags json not null default '[]',
    pattern_tags json not null default '[]',
    password_hash text,
    created_at timestamptz not null default now(),
    published_at timestamptz,
    constraint ck_case_posts_password_hash check ((visibility = 'password_protected') = (password_hash is not null))
);
create index if not exists ix_case_posts_case on case_posts (case_id);
create index if not exists ix_case_posts_demo_created on case_posts (demo_id, created_at desc, id desc);
create index if not exists ix_case_posts_feed on case_posts (published_at desc, id desc)
    where visibility = 'public' and status = 'published';

create table if not exists post_reports (
    post_id uuid not null references case_posts(id) on delete cascade,
    reporter_demo_id uuid not null,
    reason varchar(32) not null check (reason in ('personal_information','harmful_content','other')),
    created_at timestamptz not null default now(),
    primary key (post_id, reporter_demo_id)
);

-- (확장) 공감·댓글: 프론트 게시판 상세 화면용
create table if not exists post_likes (
    post_id uuid not null references case_posts(id) on delete cascade,
    demo_id uuid not null,
    created_at timestamptz not null default now(),
    primary key (post_id, demo_id)
);

create table if not exists post_comments (
    id uuid primary key default gen_random_uuid(),
    post_id uuid not null references case_posts(id) on delete cascade,
    demo_id uuid not null,
    content text not null,
    created_at timestamptz not null default now()
);
create index if not exists ix_post_comments_post on post_comments (post_id, created_at);

-- 운영자가 Supabase 관리 화면에서 status 를 published 로 바꾸면 최초 승인 시각을 기록한다.
create or replace function court.set_published_at() returns trigger language plpgsql as $$
begin
    if new.status = 'published' and new.published_at is null then
        new.published_at := now();
    end if;
    return new;
end $$;

drop trigger if exists trg_case_posts_published_at on case_posts;
create trigger trg_case_posts_published_at before insert or update of status on case_posts
    for each row execute function court.set_published_at();

-- API 서버만 DB 에 접근한다(서버는 DATABASE_URL 의 postgres 역할로 연결). Supabase 공개 API 로는 노출하지 않는다.
alter table cases enable row level security;
alter table case_evidence enable row level security;
alter table defense_turns enable row level security;
alter table case_decisions enable row level security;
alter table idempotency_keys enable row level security;
alter table case_posts enable row level security;
alter table post_reports enable row level security;
alter table post_likes enable row level security;
alter table post_comments enable row level security;
