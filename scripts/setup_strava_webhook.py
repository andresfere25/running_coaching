"""
setup_strava_webhook.py — Registra/verifica la suscripción de webhook de Strava.

Uso:
    python scripts/setup_strava_webhook.py check    # ver suscripción actual
    python scripts/setup_strava_webhook.py register # registrar nueva suscripción
    python scripts/setup_strava_webhook.py delete   # eliminar suscripción actual

Requisitos en .env:
    STRAVA_CLIENT_ID
    STRAVA_CLIENT_SECRET
    STRAVA_WEBHOOK_VERIFY_TOKEN   (mismo valor que en Railway)

El callback_url apunta a Railway:
    https://runningcoaching-production.up.railway.app/webhooks/strava
"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.getenv("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET")
VERIFY_TOKEN  = os.getenv("STRAVA_WEBHOOK_VERIFY_TOKEN", "runa_webhook_2026")
CALLBACK_URL  = "https://runningcoaching-production.up.railway.app/webhooks/strava"

SUBSCRIPTIONS_URL = "https://www.strava.com/api/v3/push_subscriptions"


def check():
    """Lista las suscripciones de webhook activas."""
    resp = requests.get(
        SUBSCRIPTIONS_URL,
        params={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
    )
    print(f"Status: {resp.status_code}")
    data = resp.json()
    if isinstance(data, list) and data:
        for sub in data:
            print(f"  ✅ Suscripción activa:")
            print(f"     id           = {sub.get('id')}")
            print(f"     callback_url = {sub.get('callback_url')}")
            print(f"     created_at   = {sub.get('created_at')}")
            print(f"     updated_at   = {sub.get('updated_at')}")
    else:
        print("  ❌ Sin suscripciones activas:", data)
    return data


def register():
    """Registra el webhook de Railway con Strava."""
    print(f"Registrando webhook...")
    print(f"  callback_url  = {CALLBACK_URL}")
    print(f"  verify_token  = {VERIFY_TOKEN}")

    resp = requests.post(
        SUBSCRIPTIONS_URL,
        data={
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "callback_url":  CALLBACK_URL,
            "verify_token":  VERIFY_TOKEN,
        },
    )
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.json()}")

    if resp.status_code == 201:
        print("✅ Webhook registrado exitosamente.")
        print("   Strava enviará POST a Railway cada vez que cualquier atleta")
        print("   conectado cree, actualice o elimine una actividad.")
    elif resp.status_code == 422:
        print("⚠️  Ya existe una suscripción activa. Usa 'check' para verla.")
    else:
        print("❌ Error al registrar. Verifica CLIENT_ID y CLIENT_SECRET en .env")


def delete():
    """Elimina la suscripción de webhook activa."""
    subs = check()
    if not isinstance(subs, list) or not subs:
        print("No hay suscripción que eliminar.")
        return

    sub_id = subs[0].get("id")
    if not sub_id:
        print("No se pudo obtener el ID de la suscripción.")
        return

    resp = requests.delete(
        f"{SUBSCRIPTIONS_URL}/{sub_id}",
        params={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
    )
    print(f"Delete status: {resp.status_code}")
    if resp.status_code == 204:
        print(f"✅ Suscripción {sub_id} eliminada.")
    else:
        print(f"❌ Error: {resp.text}")


if __name__ == "__main__":
    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ Faltan STRAVA_CLIENT_ID o STRAVA_CLIENT_SECRET en .env")
        sys.exit(1)

    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"

    if cmd == "check":
        check()
    elif cmd == "register":
        register()
    elif cmd == "delete":
        delete()
    else:
        print(f"Comando desconocido: {cmd}")
        print("Uso: python scripts/setup_strava_webhook.py [check|register|delete]")
