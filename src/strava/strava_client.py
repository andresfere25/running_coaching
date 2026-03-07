import os
import requests
from dotenv import load_dotenv


STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"


def get_strava_client_config():
    load_dotenv()
    client_id = os.getenv("STRAVA_CLIENT_ID")
    client_secret = os.getenv("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise RuntimeError("Faltan STRAVA_CLIENT_ID o STRAVA_CLIENT_SECRET en el .env")
    return str(client_id).strip(), str(client_secret).strip()


def exchange_code_for_tokens(code: str) -> dict:
    """
    Intercambia 'code' por tokens.
    Devuelve: access_token, refresh_token, expires_at, athlete (incluye id).
    """
    client_id, client_secret = get_strava_client_config()

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    }

    r = requests.post(STRAVA_TOKEN_URL, data=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Strava token exchange failed ({r.status_code}): {r.text}")

    return r.json()


def refresh_access_token(refresh_token: str) -> dict:
    """
    Usa refresh_token para obtener access_token nuevo.
    """
    client_id, client_secret = get_strava_client_config()

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    r = requests.post(STRAVA_TOKEN_URL, data=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Strava refresh failed ({r.status_code}): {r.text}")

    return r.json()