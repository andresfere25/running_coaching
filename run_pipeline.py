"""
run_pipeline.py — Orquestador maestro del pipeline de Running Coaching.

Uso:
    # Un atleta específico
    python run_pipeline.py --cedula 1070982737

    # Todos los atletas en data/athletes/
    python run_pipeline.py --all

    # Solo pasos específicos
    python run_pipeline.py --cedula 1070982737 --steps ingest features pdf

    # Sin Strava (solo Forms + check-ins)
    python run_pipeline.py --cedula 1070982737 --skip-strava
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")

# ─── Pasos del pipeline en orden ───────────────────────────────────────────
STEP_ORDER = ["ingest", "strava", "features", "plan", "pdf"]


def step_ingest_forms():
    """Ingesta del Form 1 (perfil de atletas) desde Google Sheets."""
    from src.ingest.ingest_forms import main as run
    run()


def step_ingest_checkins():
    """Ingesta del Form 2 (check-ins semanales) desde Google Sheets."""
    from src.ingest.ingest_checkins import main as run
    run()


def step_sync_strava():
    """
    Sincroniza actividades de Strava (GLOBAL).

    Nota: src.strava.sync_strava.main() NO acepta parámetros.
    El sync se basa en la hoja strava_tokens (status CONNECTED).
    """
    from src.strava.sync_strava import main as run
    run()


def step_build_features(cedula: str):
    """Calcula features semanales (ACWR, zonas, semáforo) para un atleta."""
    from src.features.build_features import main as run
    run(cedula=cedula)


def step_build_plan(cedula: str):
    """Construye el plan semanal de running + fuerza para un atleta."""
    from src.plan.plan_builder import main as run
    run(cedula=cedula)


def step_generate_pdf(cedula: str):
    """Genera el PDF del reporte de coaching para un atleta."""
    from src.reports.generate_pdf import main as run
    run(cedula=cedula)


# ─── Descubrir atletas desde data/athletes/ ────────────────────────────────
def discover_athletes(data_dir: Path) -> list[str]:
    if not data_dir.exists():
        return []
    return sorted([
        d.name for d in data_dir.iterdir()
        if d.is_dir() and d.name.isdigit()
    ])


# ─── Runner principal ───────────────────────────────────────────────────────
def run_for_athlete(cedula: str, steps: list[str], skip_strava: bool, strava_ran: bool) -> tuple[bool, bool]:
    """
    Corre el pipeline para un atleta.
    Retorna (success, strava_ran) para evitar ejecutar Strava múltiples veces.
    """
    log.info(f"{'─'*50}")
    log.info(f"🏃  Atleta: {cedula}")
    log.info(f"{'─'*50}")
    success = True
    t0 = time.time()

    step_fns = {
        "features": lambda: step_build_features(cedula),
        "plan":     lambda: step_build_plan(cedula),
        "pdf":      lambda: step_generate_pdf(cedula),
    }

    for step in steps:
        try:
            if step == "ingest":
                continue  # ingest ya se corrió globalmente

            if step == "strava":
                if skip_strava:
                    log.info("  ⏭️  Strava skipped")
                    continue

                # Ejecutar Strava SOLO UNA VEZ por corrida completa
                if not strava_ran:
                    step_sync_strava()
                    strava_ran = True
                    log.info("  ✅  strava (global)")
                else:
                    log.info("  ↪️  strava ya se ejecutó (global), se omite aquí")
                continue

            if step in step_fns:
                step_fns[step]()

            log.info(f"  ✅  {step}")

        except Exception as e:
            log.error(f"  ❌  {step}: {e}")
            success = False

    elapsed = time.time() - t0
    status = "✅ OK" if success else "❌ FALLÓ"
    log.info(f"  {status}  ({elapsed:.1f}s)")
    return success, strava_ran


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline de Running Coaching",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--cedula", type=str, help="Cédula del atleta a procesar")
    group.add_argument("--all", action="store_true", help="Procesar todos los atletas")

    parser.add_argument(
        "--steps", nargs="+",
        choices=STEP_ORDER,
        default=STEP_ORDER,
        help="Pasos a ejecutar (default: todos)",
    )
    parser.add_argument("--skip-strava", action="store_true", help="Omitir sync de Strava")
    parser.add_argument("--data-dir", default=os.getenv("DATA_DIR", "data/athletes"))

    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    steps = args.steps
    started = datetime.now()

    log.info(f"🚀  Running Coaching Pipeline  —  {started.strftime('%Y-%m-%d %H:%M')}")
    log.info(f"   Pasos: {', '.join(steps)}")

    # ── Paso global: ingesta (Forms, aplica a todos) ──
    if "ingest" in steps:
        log.info("\n📥  Ingest Forms (global)...")
        try:
            step_ingest_forms()
            log.info("  ✅  ingest_forms")
        except Exception as e:
            log.error(f"  ❌  ingest_forms: {e}")

        log.info("\n📥  Ingest Check-ins (global)...")
        try:
            step_ingest_checkins()
            log.info("  ✅  ingest_checkins")
        except Exception as e:
            log.error(f"  ❌  ingest_checkins: {e}")

    # ── Por atleta ──
    if args.all:
        cedulas = discover_athletes(data_dir)
        if not cedulas:
            log.warning(f"No se encontraron atletas en {data_dir}")
            sys.exit(1)
        log.info(f"\n👥  Atletas encontrados: {', '.join(cedulas)}")
    else:
        cedulas = [args.cedula]

    # Si vas a correr Strava, corre global una sola vez antes del loop
    strava_ran = False
    if ("strava" in steps) and (not args.skip_strava):
        log.info("\n🚴  Strava Sync (global)...")
        try:
            step_sync_strava()
            strava_ran = True
            log.info("  ✅  strava (global)")
        except Exception as e:
            log.error(f"  ❌  strava (global): {e}")
            # No hacemos sys.exit aquí; dejamos que features/plan/pdf fallen con mensaje claro.

    results = {}
    for cedula in cedulas:
        ok, strava_ran = run_for_athlete(cedula, steps, args.skip_strava, strava_ran)
        results[cedula] = ok

    # ── Resumen final ──
    elapsed = (datetime.now() - started).total_seconds()
    log.info(f"\n{'═'*50}")
    log.info(f"📊  RESUMEN  ({elapsed:.0f}s total)")
    log.info(f"{'═'*50}")
    for cedula, ok in results.items():
        log.info(f"  {'✅' if ok else '❌'}  {cedula}")

    failed = [c for c, ok in results.items() if not ok]
    if failed:
        log.error(f"\n{len(failed)} atleta(s) con errores: {', '.join(failed)}")
        sys.exit(1)
    else:
        log.info("\n✅  Todo OK. PDFs en data/athletes/<cedula>/outputs/")


if __name__ == "__main__":
    main()