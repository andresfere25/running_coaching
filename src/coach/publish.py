"""
src/coach/publish.py — CLI de publicación del contenido del coach.

Flujo:
  1. Generar draft plantilla desde el snapshot actual:
       python -m src.coach.publish --cedula 1070982737 --template

  2. Editar data/athletes/{cedula}/coach/weekly_draft.json con tu criterio.

  3. Validar y publicar:
       python -m src.coach.publish --cedula 1070982737

  4. La app leerá /athletes/{cedula}/coach-content → weekly_published.json

Claves canónicas de día: LUN MAR MIE JUE VIE SAB DOM
Los campos con prefijo '_' en el draft se excluyen del publicado.
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ─── Constantes ────────────────────────────────────────────────────────────

REQUIRED_FIELDS = ["coach_message", "weekly_focus", "week_start"]

VALID_DAY_KEYS = {"LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"}


# ─── Helpers ───────────────────────────────────────────────────────────────

def coach_dir(cedula: str, data_dir: Path) -> Path:
    return data_dir / cedula / "coach"


def load_snapshot(cedula: str, data_dir: Path) -> dict:
    snap_path = data_dir / cedula / "features" / "athlete_snapshot.json"
    if snap_path.exists():
        return json.loads(snap_path.read_text(encoding="utf-8"))
    return {}


def load_plan(cedula: str, data_dir: Path) -> dict:
    plan_path = data_dir / cedula / "features" / "weekly_plan.json"
    if plan_path.exists():
        return json.loads(plan_path.read_text(encoding="utf-8"))
    return {}


# ─── Creación de plantilla de draft ────────────────────────────────────────

def _current_monday() -> str:
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()


def make_template(cedula: str, snapshot: dict, plan: dict) -> dict:
    """
    Genera un draft pre-rellenado con el contexto automático actual.
    El coach edita este archivo y luego ejecuta --publish.
    Los campos con prefijo '_' son solo contexto — no se incluyen al publicar.
    """
    lw = snapshot.get("latest_week") or {}
    return {
        "cedula": cedula,
        "week_start": _current_monday(),
        "status": "draft",

        # ── Contenido que escribe el coach ──────────────────────────────
        "coach_message":    "Escribe aquí el mensaje principal del coach para esta semana.",
        "weekly_focus":     plan.get("weekly_focus") or snapshot.get("weekly_focus") or "",
        "weekly_objective": "",
        "alerts":           [],
        "restrictions":     [],

        # Notas por sesión — claves canónicas LUN..DOM, dejar vacío si no aplica
        "session_notes": {
            "LUN": "", "MAR": "", "MIE": "",
            "JUE": "", "VIE": "", "SAB": "", "DOM": "",
        },

        # Overrides sobre el plan automático (opcional)
        # Ejemplo: "SAB": { "km": 14, "pace": "6:00–6:30" }
        "plan_overrides": {},

        # ── Contexto automático (solo lectura, se excluye al publicar) ──
        "_auto_context": {
            "ctl":          lw.get("ctl"),
            "atl":          lw.get("atl"),
            "tsb":          lw.get("tsb"),
            "acwr":         lw.get("acwr"),
            "km_last_week": lw.get("km_week"),
            "semaforo":     snapshot.get("semaforo_latest_checkin"),
            "readiness":    snapshot.get("readiness_score"),
            "week_type":    plan.get("week_type"),
            "target_km":    (plan.get("targets") or {}).get("target_km_week"),
        },
        "_instructions": (
            "Edita los campos de contenido arriba. "
            "Elimina las session_notes vacías ('') o déjalas — se ignoran. "
            "Los campos con '_' son solo contexto y no se publican. "
            "Ejecuta: python -m src.coach.publish --cedula " + cedula
        ),
    }


# ─── Validación ────────────────────────────────────────────────────────────

def validate_draft(draft: dict) -> list[str]:
    errors = []

    for field in REQUIRED_FIELDS:
        if not draft.get(field):
            errors.append(f"Campo requerido vacío o ausente: '{field}'")

    for section in ("session_notes", "plan_overrides"):
        for key in (draft.get(section) or {}):
            if key not in VALID_DAY_KEYS:
                errors.append(
                    f"Clave de día inválida en '{section}': '{key}'. "
                    f"Válidas: {sorted(VALID_DAY_KEYS)}"
                )

    return errors


# ─── Publicación ───────────────────────────────────────────────────────────

def publish(cedula: str, data_dir: Path) -> None:
    d = coach_dir(cedula, data_dir)
    draft_path = d / "weekly_draft.json"
    published_path = d / "weekly_published.json"

    if not draft_path.exists():
        print(f"❌  No se encontró draft en: {draft_path}")
        print(f"    Crea uno con: python -m src.coach.publish --cedula {cedula} --template")
        sys.exit(1)

    try:
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"❌  El draft no es JSON válido: {e}")
        sys.exit(1)

    errors = validate_draft(draft)
    if errors:
        print("❌  Errores de validación — corrige el draft antes de publicar:")
        for err in errors:
            print(f"    • {err}")
        sys.exit(1)

    # Filtrar campos privados (_) y limpiar notas de sesión vacías
    published = {k: v for k, v in draft.items() if not k.startswith("_")}
    published["status"] = "published"
    published["published_at"] = datetime.now(timezone.utc).isoformat()

    # Eliminar session_notes vacías para mantener el JSON limpio
    notes = published.get("session_notes") or {}
    published["session_notes"] = {k: v for k, v in notes.items() if v and v.strip()}

    published_path.write_text(
        json.dumps(published, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"✅  Publicado: {published_path}")
    print(f"    Semana: {published.get('week_start')} · Atleta: {cedula}")
    print(f"    Endpoint: /athletes/{cedula}/coach-content")

    # Push a Supabase si está configurado (dual-write)
    try:
        from src.storage.supabase_client import is_configured
        if is_configured():
            from src.storage.writer import push_coach_content
            result = push_coach_content(cedula, published)
            if result.get("ok"):
                print(f"    Supabase: ✅ {result['detail']}")
            else:
                print(f"    Supabase: ⚠  {result['detail']} (publicado solo local)")
        else:
            print("    Supabase: no configurado — publicado solo en local.")
    except Exception as exc:
        print(f"    Supabase: ⚠  error al pushear ({exc}) — publicado solo local.")


# ─── CLI ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI de publicación del contenido del coach",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python -m src.coach.publish --cedula 1070982737 --template\n"
            "  python -m src.coach.publish --cedula 1070982737"
        ),
    )
    parser.add_argument("--cedula", required=True, help="Cédula del atleta")
    parser.add_argument(
        "--data-dir", default="data/athletes", help="Directorio base de atletas"
    )
    parser.add_argument(
        "--template",
        action="store_true",
        help="Crear draft plantilla desde el snapshot actual (para editar manualmente)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    d = coach_dir(args.cedula, data_dir)
    d.mkdir(parents=True, exist_ok=True)

    if args.template:
        snapshot = load_snapshot(args.cedula, data_dir)
        plan = load_plan(args.cedula, data_dir)
        template = make_template(args.cedula, snapshot, plan)
        draft_path = d / "weekly_draft.json"
        draft_path.write_text(
            json.dumps(template, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"✅  Draft creado: {draft_path}")
        print(f"    Edítalo y luego ejecuta:")
        print(f"    python -m src.coach.publish --cedula {args.cedula}")
        return

    publish(args.cedula, data_dir)


if __name__ == "__main__":
    main()
