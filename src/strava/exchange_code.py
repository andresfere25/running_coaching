import os
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv

from src.ingest.sheets_client import open_sheet, read_worksheet_as_records, write_worksheet_from_df
from src.ingest.parsers import norm_str
from src.strava.strava_client import exchange_code_for_tokens

ONBOARD_TAB = "strava_onboarding"
TOKENS_TAB = "strava_tokens"


TOKENS_COLS = [
    "code",
    "cedula",
    "athlete_id",
    "refresh_token",
    "scope",
    "connected_at",
    "last_sync_date",
    "status (CONNECTED / REVOKED / ERROR)",
]


def upsert_tokens_row(existing_df: pd.DataFrame, new_row: dict) -> pd.DataFrame:
    """Upsert por cédula."""
    if existing_df.empty:
        return pd.DataFrame([new_row], columns=TOKENS_COLS)

    # asegurar columnas
    for c in TOKENS_COLS:
        if c not in existing_df.columns:
            existing_df[c] = ""

    mask = existing_df["cedula"].astype(str).str.strip() == str(new_row["cedula"]).strip()
    if mask.any():
        for k, v in new_row.items():
            existing_df.loc[mask, k] = v
        return existing_df
    else:
        return pd.concat([existing_df, pd.DataFrame([new_row], columns=TOKENS_COLS)], ignore_index=True)


def already_connected(df_tokens: pd.DataFrame, cedula: str) -> bool:
    """Retorna True si ya está CONNECTED y tiene refresh_token."""
    if df_tokens.empty:
        return False
    if "cedula" not in df_tokens.columns or "status (CONNECTED / REVOKED / ERROR)" not in df_tokens.columns:
        return False

    mask = df_tokens["cedula"].astype(str).str.strip() == str(cedula).strip()
    if not mask.any():
        return False

    status = str(df_tokens.loc[mask, "status (CONNECTED / REVOKED / ERROR)"].iloc[0]).strip().upper()
    refresh = ""
    if "refresh_token" in df_tokens.columns:
        refresh = str(df_tokens.loc[mask, "refresh_token"].iloc[0]).strip()

    return status == "CONNECTED" and refresh != ""


def main():
    load_dotenv()
    sheet_id = os.getenv("SHEET_ID")
    sa_json = os.getenv("GOOGLE_SA_JSON")

    sheet = open_sheet(sheet_id, sa_json)

    onboard = read_worksheet_as_records(sheet, ONBOARD_TAB)
    if not onboard:
        raise RuntimeError(f"No hay datos en {ONBOARD_TAB}")

    df_on = pd.DataFrame(onboard)

    # Columnas reales
    c_ced = "cedula"
    c_code = "code (extraído del link)"
    c_scope = "scope (extraído del link)"
    c_status = "status (PENDIENTE / OK / ERROR)"

    for required in [c_ced, c_code]:
        if required not in df_on.columns:
            raise RuntimeError(f"Falta columna '{required}' en {ONBOARD_TAB}")

    # Filtrar OK con code
    df_ok = df_on.copy()
    if c_status in df_ok.columns:
        df_ok = df_ok[df_ok[c_status].astype(str).str.upper().str.contains("OK", na=False)]
    df_ok = df_ok[df_ok[c_code].astype(str).str.strip() != ""]

    if df_ok.empty:
        print("ℹ️ No hay filas OK con code en strava_onboarding.")
        return

    # Leer tokens existentes
    tokens_records = read_worksheet_as_records(sheet, TOKENS_TAB)
    df_tokens = pd.DataFrame(tokens_records) if tokens_records else pd.DataFrame(columns=TOKENS_COLS)

    updates = 0

    for _, row in df_ok.iterrows():
        cedula = norm_str(row.get(c_ced))
        code = norm_str(row.get(c_code))
        scope = norm_str(row.get(c_scope)) if c_scope in df_ok.columns else ""

        if not cedula or not code:
            continue

        # Evitar re-exchange
        if already_connected(df_tokens, cedula):
            print(f"ℹ️ Ya CONNECTED: {cedula}. No se re-ejecuta exchange.")
            continue

        try:
            token_json = exchange_code_for_tokens(code)
            athlete_id = token_json.get("athlete", {}).get("id")
            refresh_token = token_json.get("refresh_token")

            new_row = {
                "code": code,
                "cedula": cedula,
                "athlete_id": str(athlete_id) if athlete_id else "",
                "refresh_token": refresh_token or "",
                "scope": scope,
                "connected_at": datetime.now().isoformat(timespec="seconds"),
                "last_sync_date": "",
                "status (CONNECTED / REVOKED / ERROR)": "CONNECTED" if refresh_token else "ERROR",
            }

            df_tokens = upsert_tokens_row(df_tokens, new_row)
            updates += 1
            print(f"✅ CONNECTED: {cedula} athlete_id={athlete_id}")

        except Exception as e:
            # Guardar status ERROR para trazabilidad
            err_row = {
                "code": code,
                "cedula": cedula,
                "athlete_id": "",
                "refresh_token": "",
                "scope": scope,
                "connected_at": datetime.now().isoformat(timespec="seconds"),
                "last_sync_date": "",
                "status (CONNECTED / REVOKED / ERROR)": "ERROR",
            }
            df_tokens = upsert_tokens_row(df_tokens, err_row)
            print(f"❌ ERROR conectando {cedula}: {e}")

    # Escribir tokens a Google Sheets (SA es Editor)
    write_worksheet_from_df(sheet, TOKENS_TAB, df_tokens)

    print(f"\n✅ Listo. Filas procesadas: {updates}")
    print("✅ strava_tokens actualizado automáticamente en Google Sheets.")


if __name__ == "__main__":
    main()