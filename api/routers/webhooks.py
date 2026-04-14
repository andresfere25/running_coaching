"""
api/routers/webhooks.py — Strava Webhook Event Subscription handler.

Endpoints:
  GET  /webhooks/strava  — Strava hub.challenge verification (one-time setup)
  POST /webhooks/strava  — Strava event handler (activity + athlete events)

Events handled:
  - athlete deauthorize
      object_type=athlete, aspect_type=update, updates.authorized=false
      → Delete ALL Strava-derived data for this athlete (Strava API Agreement §5.4)
  - activity delete
      object_type=activity, aspect_type=delete
      → Remove activity from local parquet and Supabase if stored (§7)

Events intentionally ignored (handled by scheduled sync):
  - activity create / activity update → next scheduled sync will pick them up.

Setup:
  1. Set STRAVA_WEBHOOK_VERIFY_TOKEN in .env (any secret string you choose).
  2. Register the webhook with Strava:
       POST https://www.strava.com/api/v3/push_subscriptions
         client_id=<STRAVA_CLIENT_ID>
         client_secret=<STRAVA_CLIENT_SECRET>
         callback_url=https://<your-domain>/webhooks/strava
         verify_token=<STRAVA_WEBHOOK_VERIFY_TOKEN>
  3. Strava will call GET /webhooks/strava?hub.mode=subscribe&hub.challenge=...
     — the endpoint will echo the challenge back to complete verification.
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request, Response

from api.deps import get_data_dir

logger = logging.getLogger(__name__)

router = APIRouter()

# Secret token you set when registering the Strava webhook subscription.
_VERIFY_TOKEN = os.environ.get("STRAVA_WEBHOOK_VERIFY_TOKEN", "")


# ─── GET: hub.challenge verification ─────────────────────────────────────────

@router.get("/webhooks/strava")
def strava_webhook_verify(request: Request):
    """
    One-time verification called by Strava when registering the webhook.
    Returns hub.challenge if hub.verify_token matches STRAVA_WEBHOOK_VERIFY_TOKEN.
    """
    params    = dict(request.query_params)
    mode      = params.get("hub.mode")
    challenge = params.get("hub.challenge")
    token     = params.get("hub.verify_token")

    if mode == "subscribe" and challenge and token == _VERIFY_TOKEN:
        logger.info("[webhook] Strava verification OK")
        return {"hub.challenge": challenge}

    logger.warning(
        "[webhook] Strava verification FAILED — mode=%s token_match=%s",
        mode, token == _VERIFY_TOKEN,
    )
    return Response(status_code=403)


# ─── POST: event handler ──────────────────────────────────────────────────────

@router.post("/webhooks/strava", status_code=200)
async def strava_webhook_event(request: Request, background: BackgroundTasks):
    """
    Receives Strava event notifications. Must return 200 within 2 seconds.
    All processing is deferred to background tasks.
    """
    try:
        event = await request.json()
    except Exception:
        logger.warning("[webhook] Could not parse event body")
        return {"ok": False}

    object_type = event.get("object_type")
    aspect_type = event.get("aspect_type")
    owner_id    = event.get("owner_id")   # Strava athlete ID (int)
    object_id   = event.get("object_id") # activity_id or athlete_id

    logger.info(
        "[webhook] event object_type=%s aspect_type=%s owner_id=%s object_id=%s",
        object_type, aspect_type, owner_id, object_id,
    )

    # ── Athlete deauthorize ───────────────────────────────────────────────────
    # Strava sends: object_type=athlete, aspect_type=update, updates.authorized=false
    updates   = event.get("updates", {})
    is_deauth = (
        object_type == "athlete"
        and aspect_type == "update"
        and str(updates.get("authorized", "")).lower() == "false"
    )
    if is_deauth and owner_id is not None:
        background.add_task(_handle_deauthorize, int(owner_id))
        return {"ok": True}

    # ── Activity delete ───────────────────────────────────────────────────────
    if (
        object_type == "activity"
        and aspect_type == "delete"
        and object_id is not None
        and owner_id is not None
    ):
        background.add_task(_handle_activity_delete, int(owner_id), int(object_id))
        return {"ok": True}

    # ── Activity create → trigger pipeline en background ─────────────────────
    if (
        object_type == "activity"
        and aspect_type == "create"
        and owner_id is not None
    ):
        background.add_task(_handle_activity_create, int(owner_id))
        return {"ok": True}

    # Other events (update) — next scheduled sync will pick them up.
    return {"ok": True}


# ─── Background: deauthorize ─────────────────────────────────────────────────

def _handle_deauthorize(strava_athlete_id: int) -> None:
    """
    Full data cleanup on Strava deauthorize (§5.4 compliance).

    Steps:
      1. Find cedula from strava_athlete_id (Supabase → local fallback)
      2. Delete Strava-derived rows from Supabase:
           activities, weekly_features, athlete_snapshots, weekly_plans
         (athlete_profiles and checkins are from Google Forms — NOT deleted)
      3. Clear strava_athlete_id from athletes table (keep row for reconnect)
      4. Delete local Strava-derived files (parquet + feature JSONs)
      5. Mark REVOKED in Google Sheets strava_tokens (best-effort)
    """
    logger.info("[webhook/deauth] Processing deauthorize strava_id=%s", strava_athlete_id)

    cedula = _find_cedula(strava_athlete_id)
    if not cedula:
        logger.warning(
            "[webhook/deauth] strava_id=%s not found — no data to delete",
            strava_athlete_id,
        )
        return

    logger.info("[webhook/deauth] Found cedula=%s for strava_id=%s", cedula, strava_athlete_id)

    _delete_supabase_strava_data(cedula, strava_athlete_id)
    _delete_local_strava_files(cedula)
    _mark_revoked_in_supabase(cedula)

    logger.info("[webhook/deauth] Cleanup complete for cedula=%s", cedula)


def _find_cedula(strava_athlete_id: int) -> str | None:
    """
    Map strava_athlete_id → cedula.
    Primary: Supabase athletes.strava_athlete_id column.
    Fallback: scan local raw parquet files (slower, used if Supabase unavailable).
    """
    cedula = _find_cedula_supabase(strava_athlete_id)
    if cedula:
        return cedula
    return _find_cedula_local(strava_athlete_id)


def _find_cedula_supabase(strava_athlete_id: int) -> str | None:
    from src.storage.supabase_client import get_client
    client = get_client()
    if not client:
        return None
    try:
        resp = (
            client.table("athletes")
            .select("cedula")
            .eq("strava_athlete_id", str(strava_athlete_id))
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0]["cedula"]
    except Exception as exc:
        logger.warning("[webhook/deauth] Supabase lookup failed: %s", exc)
    return None


def _find_cedula_local(strava_athlete_id: int) -> str | None:
    """
    Scan raw/strava_activities_raw.parquet in each athlete dir.
    Each row from the Strava API has an `athlete` struct with an `id` field.
    """
    import duckdb

    data_dir = get_data_dir()
    if not data_dir.exists():
        return None

    for athlete_dir in data_dir.iterdir():
        if not athlete_dir.is_dir():
            continue
        raw_parquet = athlete_dir / "raw" / "strava_activities_raw.parquet"
        if not raw_parquet.exists():
            continue
        try:
            result = duckdb.query(
                f"SELECT 1 FROM '{raw_parquet.as_posix()}' "
                f"WHERE athlete.id = {strava_athlete_id} LIMIT 1"
            ).fetchone()
            if result:
                return athlete_dir.name  # dir name = cedula
        except Exception:
            continue

    return None


def _delete_supabase_strava_data(cedula: str, strava_athlete_id: int) -> None:
    """
    Delete all Strava-derived rows from Supabase for this cedula.
    Tables deleted: activities, weekly_features, athlete_snapshots, weekly_plans.
    Tables kept:    athlete_profiles, checkins (sourced from Google Forms, not Strava).
    """
    from src.storage.supabase_client import get_client
    client = get_client()
    if not client:
        logger.warning("[webhook/deauth] Supabase not configured — remote delete skipped")
        return

    strava_tables = ["activities", "weekly_features", "athlete_snapshots", "weekly_plans"]
    for table in strava_tables:
        try:
            client.table(table).delete().eq("cedula", cedula).execute()
            logger.info("[webhook/deauth] Deleted Supabase %s for cedula=%s", table, cedula)
        except Exception as exc:
            logger.warning("[webhook/deauth] Failed to delete Supabase %s: %s", table, exc)

    # Clear strava_athlete_id so the athlete can reconnect with a fresh record.
    try:
        client.table("athletes").update({"strava_athlete_id": None}).eq("cedula", cedula).execute()
        logger.info("[webhook/deauth] Cleared strava_athlete_id for cedula=%s", cedula)
    except Exception as exc:
        logger.warning("[webhook/deauth] Failed to clear strava_athlete_id: %s", exc)


def _delete_local_strava_files(cedula: str) -> None:
    """
    Delete local Strava-derived files for this athlete.
    Files deleted: raw parquet, silver parquet, feature files derived from activities.
    Files kept:    meta/profile.json, meta/latest_checkin.json (from Forms), coach/ content.
    """
    athlete_dir = get_data_dir() / cedula
    if not athlete_dir.exists():
        return

    targets = [
        athlete_dir / "raw"      / "strava_activities_raw.parquet",
        athlete_dir / "silver"   / "activities.parquet",
        athlete_dir / "features" / "weekly_features.parquet",
        athlete_dir / "features" / "athlete_snapshot.json",
        athlete_dir / "features" / "weekly_plan.json",
    ]

    for f in targets:
        if f.exists():
            try:
                f.unlink()
                logger.info("[webhook/deauth] Deleted local file: %s", f.name)
            except Exception as exc:
                logger.warning("[webhook/deauth] Failed to delete %s: %s", f.name, exc)


def _mark_revoked_in_supabase(cedula: str) -> None:
    """Best-effort: clear Strava tokens in Supabase for revoked athlete."""
    try:
        from src.storage.supabase_client import get_client
        client = get_client()
        if not client:
            return

        client.table("athletes").update({
            "strava_access_token": None,
            "strava_refresh_token": None,
            "strava_token_expires_at": None,
        }).eq("cedula", str(cedula)).execute()
        logger.info("[webhook/deauth] Cleared Strava tokens in Supabase for cedula=%s", cedula)

    except Exception as exc:
        logger.warning("[webhook/deauth] Failed to clear tokens in Supabase: %s", exc)


# ─── Background: activity delete ─────────────────────────────────────────────

def _handle_activity_delete(strava_athlete_id: int, activity_id: int) -> None:
    """
    Remove a deleted Strava activity from local parquet (and Supabase if stored).
    Per Strava API Agreement §7: data changes must be reflected promptly.
    """
    logger.info(
        "[webhook/delete] activity_id=%s strava_athlete_id=%s",
        activity_id, strava_athlete_id,
    )

    cedula = _find_cedula(strava_athlete_id)
    if not cedula:
        logger.warning("[webhook/delete] strava_id=%s not found — skipping", strava_athlete_id)
        return

    _remove_activity_from_parquet(cedula, activity_id)
    _remove_activity_from_supabase(activity_id)


def _remove_activity_from_parquet(cedula: str, activity_id: int) -> None:
    """Remove a single activity_id row from silver/activities.parquet."""
    import duckdb

    parquet = get_data_dir() / cedula / "silver" / "activities.parquet"
    if not parquet.exists():
        return

    try:
        df = duckdb.query(f"SELECT * FROM '{parquet.as_posix()}'").to_df()
        if "activity_id" not in df.columns:
            return

        before = len(df)
        df     = df[df["activity_id"] != activity_id]

        if len(df) < before:
            from src.strava.sync_strava import write_parquet_duckdb
            write_parquet_duckdb(df, parquet)
            logger.info(
                "[webhook/delete] Removed activity_id=%s from parquet cedula=%s",
                activity_id, cedula,
            )
    except Exception as exc:
        logger.warning("[webhook/delete] Failed to update parquet: %s", exc)


def _handle_activity_create(strava_athlete_id: int) -> None:
    """
    Dispara el pipeline completo cuando Strava notifica una actividad nueva.
    Busca la cédula del atleta y corre strava → features → plan con push a Supabase.
    """
    logger.info("[webhook/create] nueva actividad strava_id=%s", strava_athlete_id)

    cedula = _find_cedula(strava_athlete_id)
    if not cedula:
        logger.warning("[webhook/create] strava_id=%s no encontrado — ignorando", strava_athlete_id)
        return

    logger.info("[webhook/create] disparando pipeline para cedula=%s", cedula)
    try:
        from api.routers.sync import _run_pipeline_and_push
        result = _run_pipeline_and_push(
            cedula,
            steps=["strava", "features", "plan"],
            skip_strava=False,
            push_to_supabase=True,
        )
        logger.info("[webhook/create] pipeline cedula=%s ok=%s", cedula, result.get("ok"))
    except Exception as exc:
        logger.error("[webhook/create] pipeline cedula=%s falló: %s", cedula, exc)


def _remove_activity_from_supabase(activity_id: int) -> None:
    """Remove a single activity from Supabase activities table (if stored)."""
    from src.storage.supabase_client import get_client
    client = get_client()
    if not client:
        return
    try:
        client.table("activities").delete().eq("strava_id", activity_id).execute()
        logger.info("[webhook/delete] Removed activity_id=%s from Supabase", activity_id)
    except Exception as exc:
        logger.warning("[webhook/delete] Failed to remove from Supabase: %s", exc)
