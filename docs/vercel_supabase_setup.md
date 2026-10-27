# Vercel and Supabase setup

The dashboard and its three API functions are in `vercel_dashboard/`. Configure the existing Vercel project with that root directory. It uses Vercel's built-in Node.js Functions and static file hosting; no build command or npm dependency is required.

## 1. Create the Supabase table

Create or select a Supabase project, open **SQL Editor**, and run [`supabase_activity_predictions.sql`](supabase_activity_predictions.sql). The `activity_predictions` table stores activity predictions with cloud-generated row IDs. Row-level security is enabled; public and authenticated clients have no table access. Vercel functions use the service-role key server-side.

For an existing table, run [`supabase_activity_predictions_migration.sql`](supabase_activity_predictions_migration.sql) before deploying the current API. It preserves existing rows, adds cloud-generated row IDs and a separate local prediction ID for retry deduplication, and supports ordering by prediction time.

## 2. Set Vercel environment variables

Add these to the Vercel project for the Production environment (and Preview only if you intend to test there):

- `SUPABASE_URL` — the HTTPS URL shown in Supabase Project Settings → API.
- `SUPABASE_SERVICE_ROLE_KEY` — the private Supabase secret key (`sb_secret_...`). Keep it in Vercel server environment variables only; never use a `NEXT_PUBLIC_`/`VITE_` name or place it in frontend files. The API sends this key using the `apikey` header only because new secret keys are not JWTs. Legacy JWT-format `service_role` keys remain supported with both `apikey` and bearer headers.
- `INGEST_TOKEN` — a new, high-entropy private token used only by the local uploader and `/api/ingest`. Store it in a password manager and set the same value as `ACTIVITY_INGEST_TOKEN` in the uploader's environment. Never commit it.

Redeploy after setting the variables. The dashboard calls same-origin `/api/status` and `/api/activities`; the uploader posts to same-origin `/api/ingest`. The API functions return an unavailable/configuration response until the Vercel variables and Supabase table are ready.

## 3. API routes

- `GET /api/status` — latest prediction by timestamp, its attached sensor timestamp, age, and `live`, `stale`, or `waiting` state. Freshness uses the same 10-second threshold as the local Flask API.
- `GET /api/activities` — latest 50 prediction rows, newest first by timestamp.
- `POST /api/ingest` — accepts one prediction only with `Authorization: Bearer <INGEST_TOKEN>`. The cloud assigns its own row ID; repeated local prediction IDs with the same timestamp are idempotently ignored. `motion_heuristic` predictions must have `confidence: null`.

The service-role key never goes to the browser. The ingestion token is required only by the Python uploader and is not part of dashboard requests. Sensor readings are not uploaded to Supabase; each prediction carries the timestamp of the latest local sensor reading used for that inference.

## 4. Run the live uploader

The uploader uses Python's standard library and opens the existing SQLite database read-only. Set `ACTIVITY_DB_PATH` to the same database used by the MQTT subscriber/classifier and `ACTIVITY_API_URL` to the Vercel deployment origin. If `ACTIVITY_INGEST_TOKEN` is unset, the uploader prompts for the token without echoing it. Then run:

```powershell
$env:ACTIVITY_DB_PATH = 'C:\path\to\the\existing\activities.sqlite'
$env:ACTIVITY_API_URL = 'https://your-vercel-domain.example'
python scripts/upload_live_predictions.py
```

The uploader records the current maximum prediction and sensor IDs when it starts and skips all older rows. While running, it forwards only subsequent predictions whose prediction and sensor timestamps are within 10 seconds. It never modifies SQLite and does not upload raw sensor values. If it stops, restarting establishes a new high-water mark, so missed rows are not replayed as live.

## 5. Hardware limitation

This cloud API is downstream of the existing classifier; it does not connect the RT-Spark to the laptop. The board's current COM3 diagnostics do not contain the six accelerometer/gyroscope values, and its MQTT connection to the laptop broker has been blocked by the managed inbound policy. Until the managed policy source authorizes the specific RT-Spark-to-Mosquitto route (or an approved USB path supplies genuine six-axis readings), the classifier will produce no new hardware predictions for the uploader. Do not infer live operation from stored rows or from a successful Vercel deployment.
