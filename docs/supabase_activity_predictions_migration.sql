-- Apply once in the Supabase SQL Editor before deploying the timestamp and
-- collision-safe ingestion code. Existing prediction rows and IDs are kept.
alter table public.activity_predictions
    add column if not exists local_prediction_id bigint;

-- Give cloud rows their own monotonically allocated IDs, starting above every
-- existing ID. Existing rows remain unchanged.
create sequence if not exists public.activity_predictions_id_seq;
select setval(
    'public.activity_predictions_id_seq',
    greatest(
        coalesce((select max(id) from public.activity_predictions), 0),
        (select last_value from public.activity_predictions_id_seq)
    ),
    true
);
alter table public.activity_predictions
    alter column id set default nextval('public.activity_predictions_id_seq');
alter sequence public.activity_predictions_id_seq
    owned by public.activity_predictions.id;

-- Retries of the same local prediction are ignored. A restarted local SQLite
-- ID with a different timestamp remains a distinct row.
create unique index if not exists activity_predictions_local_prediction_timestamp_uidx
    on public.activity_predictions (local_prediction_id, timestamp);

create index if not exists activity_predictions_timestamp_desc_idx
    on public.activity_predictions (timestamp desc, id desc);
