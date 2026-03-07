import os
import json
from pathlib import Path
from datetime import datetime
import duckdb
import pandas as pd
import matplotlib.pyplot as plt
from dotenv import load_dotenv

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Image
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER


def read_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return duckdb.query(f"SELECT * FROM '{path.as_posix()}'").to_df()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_chart(fig, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


def pace_str(sec_per_km):
    if sec_per_km is None or pd.isna(sec_per_km):
        return "N/A"
    sec_per_km = float(sec_per_km)
    m = int(sec_per_km // 60)
    s = int(round(sec_per_km - 60 * m))
    return f"{m}:{s:02d} min/km"


def semaforo_color(semaforo: str):
    s = (semaforo or "").upper()
    if s == "VERDE":
        return colors.green
    if s == "AMARILLO":
        return colors.orange
    if s == "ROJO":
        return colors.red
    if s == "SIN_CHECKIN":
        return colors.grey
    return colors.grey


def build_kpi_table(latest_week: dict, semaforo: str):
    data = [
        ["Estado (check-in)", semaforo],
        ["Km semana", f"{latest_week.get('km_week', 'N/A')}"],
        ["Sesiones", f"{latest_week.get('sessions_week', 'N/A')}"],
        ["Tiempo total (min)", f"{latest_week.get('time_min_week', 'N/A')}"],
        ["Fondo (km)", f"{latest_week.get('long_run_km', 'N/A')}"],
        ["Ritmo promedio semanal", pace_str(latest_week.get("pace_sec_per_km_week"))],
        ["ACWR", f"{latest_week.get('acwr', 'N/A')}"],
        ["Monotonía", f"{latest_week.get('monotony', 'N/A')}"],
        ["Strain", f"{latest_week.get('strain', 'N/A')}"],
    ]
    return data


def plan_to_table(plan_by_day: dict):
    rows = [["Día", "Running", "Fuerza", "Notas"]]
    week_order = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
    for d in week_order:
        items = plan_by_day.get(d, [])
        run_txt = []
        str_txt = []
        notes = []

        for it in items:
            if it.get("type") == "Running":
                run_txt.append(f"- {it.get('session')}: {it.get('km','')} km @ {it.get('pace','')}")
            elif it.get("type") == "Fuerza":
                exs = it.get("details", [])
                exs_txt = "; ".join(exs[:4]) + ("..." if len(exs) > 4 else "")
                str_txt.append(f"- {it.get('session')}: {exs_txt}")

        if not items:
            notes.append("Descanso / movilidad (10–15 min)")

        rows.append([d, "\n".join(run_txt) or "-", "\n".join(str_txt) or "-", "\n".join(notes) or "-"])
    return rows


def chart_km_by_week(df_weekly: pd.DataFrame, out_path: Path):
    df = df_weekly.copy()
    df["week_start_dt"] = pd.to_datetime(df["week_start"], errors="coerce")
    df = df.sort_values("week_start_dt").tail(12)

    fig = plt.figure()
    plt.plot(df["week_start_dt"], df["km_week"])
    plt.title("Km por semana (últimas 12)")
    plt.xlabel("Semana")
    plt.ylabel("Km")
    save_chart(fig, out_path)


def chart_pace_by_week(df_weekly: pd.DataFrame, out_path: Path):
    df = df_weekly.copy()
    df["week_start_dt"] = pd.to_datetime(df["week_start"], errors="coerce")
    df = df.sort_values("week_start_dt").tail(12)

    fig = plt.figure()
    plt.plot(df["week_start_dt"], df["pace_sec_per_km_week"])
    plt.title("Ritmo promedio semanal (seg/km) - últimas 12")
    plt.xlabel("Semana")
    plt.ylabel("seg/km (más bajo = más rápido)")
    save_chart(fig, out_path)


def chart_load_risk(df_weekly: pd.DataFrame, out_path: Path):
    df = df_weekly.copy()
    df["week_start_dt"] = pd.to_datetime(df["week_start"], errors="coerce")
    df = df.sort_values("week_start_dt").tail(12)

    fig = plt.figure()
    plt.plot(df["week_start_dt"], df["acwr"], label="ACWR")
    plt.plot(df["week_start_dt"], df["monotony"], label="Monotonía")
    plt.title("Riesgo de carga (ACWR y Monotonía) - últimas 12")
    plt.xlabel("Semana")
    plt.ylabel("Valor")
    plt.legend()
    save_chart(fig, out_path)


def chart_zones(df_weekly: pd.DataFrame, out_path: Path):
    df = df_weekly.copy()
    df = df.tail(1)
    if df.empty:
        return
    row = df.iloc[0]
    z = [
        float(row.get("pctZ1") or 0),
        float(row.get("pctZ2") or 0),
        float(row.get("pctZ3") or 0),
        float(row.get("pctZ4") or 0),
    ]

    fig = plt.figure()
    plt.bar(["Z1","Z2","Z3","Z4"], z)
    plt.title("Distribución por zonas (última semana, aprox)")
    plt.ylabel("Proporción")
    save_chart(fig, out_path)


def generate_pdf(cedula: str):
    data_dir = Path(os.getenv("DATA_DIR", "data/athletes"))
    athlete_dir = data_dir / cedula

    profile = load_json(athlete_dir / "meta" / "profile.json")
    latest_checkin = load_json(athlete_dir / "meta" / "latest_checkin.json")
    weekly = read_parquet(athlete_dir / "features" / "weekly_features.parquet")
    plan = load_json(athlete_dir / "features" / "weekly_plan.json")

    if weekly.empty or not plan or not profile:
        raise RuntimeError("Faltan insumos: ejecuta build_features y plan_builder primero.")

    # Snapshot latest week
    weekly_sorted = weekly.copy()
    weekly_sorted["week_start_dt"] = pd.to_datetime(weekly_sorted["week_start"], errors="coerce")
    weekly_sorted = weekly_sorted.sort_values("week_start_dt")
    latest_week = weekly_sorted.tail(1).iloc[0].to_dict()

    semaforo = (plan.get("semaforo") or "SIN_CHECKIN")
    week_type = plan.get("week_type", "")

    # Output
    out_dir = athlete_dir / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pdf = out_dir / f"{datetime.now().date().isoformat()}_coaching_report.pdf"

    # Temp images
    img_dir = athlete_dir / "outputs" / "_charts"
    img_dir.mkdir(parents=True, exist_ok=True)

    km_chart = img_dir / "km_week.png"
    pace_chart = img_dir / "pace_week.png"
    load_chart = img_dir / "load_risk.png"
    zones_chart = img_dir / "zones.png"

    chart_km_by_week(weekly_sorted, km_chart)
    chart_pace_by_week(weekly_sorted, pace_chart)
    chart_load_risk(weekly_sorted, load_chart)
    chart_zones(weekly_sorted, zones_chart)

    # Styles
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1", parent=styles["Heading1"], alignment=TA_CENTER, spaceAfter=10))
    styles.add(ParagraphStyle(name="H2", parent=styles["Heading2"], spaceAfter=6))
    styles.add(ParagraphStyle(name="Body", parent=styles["BodyText"], leading=14, spaceAfter=6))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=9, leading=11, spaceAfter=4))
    styles.add(ParagraphStyle(name="Center", parent=styles["BodyText"], alignment=TA_CENTER))

    doc = SimpleDocTemplate(str(out_pdf), pagesize=letter, rightMargin=48, leftMargin=48, topMargin=48, bottomMargin=48)
    story = []

    # --- Portada ---
    story.append(Paragraph("Running Coaching Report", styles["H1"]))
    story.append(Paragraph(f"{profile.get('name','')} | Cédula: {cedula}", styles["Center"]))
    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph(f"Objetivo: {profile.get('goal_main','N/A')} | Carrera: {profile.get('race_distance','N/A')} | Fecha: {profile.get('race_date_raw','N/A')}", styles["Body"]))
    story.append(Paragraph(f"Semana tipo: <b>{week_type}</b> | Estado (check-in): <b>{semaforo}</b>", styles["Body"]))
    story.append(Spacer(1, 0.25 * inch))

    # Semáforo visual
    sem_color = semaforo_color(semaforo)
    sem_table = Table([[f"ESTADO: {semaforo}"]], colWidths=[6.2*inch])
    sem_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (0,0), sem_color),
        ("TEXTCOLOR", (0,0), (0,0), colors.white),
        ("FONTNAME", (0,0), (0,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (0,0), 16),
        ("ALIGN", (0,0), (0,0), "CENTER"),
        ("BOX", (0,0), (0,0), 1, colors.black),
        ("PADDING", (0,0), (0,0), 10),
    ]))
    story.append(sem_table)
    story.append(PageBreak())

    # --- Resumen ejecutivo ---
    story.append(Paragraph("Resumen ejecutivo", styles["H2"]))
    story.append(Paragraph(
        "Este reporte resume tu historial reciente, tu estado actual (check-in) y el plan recomendado para la próxima semana. "
        "El objetivo es darte claridad y decisiones concretas: qué mantener, qué mejorar y qué cuidar.",
        styles["Body"]
    ))

    kpis = build_kpi_table(latest_week, semaforo)
    kpi_table = Table(kpis, colWidths=[2.6*inch, 3.6*inch])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("BOX", (0,0), (-1,-1), 0.5, colors.black),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.grey),
        ("FONTNAME", (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("PADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(Spacer(1, 0.15 * inch))
    story.append(kpi_table)

    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph("<b>3 focos de la semana</b>", styles["Body"]))
    focos = [
        "1) Mantener consistencia (cumplir los días objetivo).",
        "2) Controlar carga (no subir si el cuerpo pide pausa).",
        "3) Priorizar el fondo como base aeróbica y fuerza para prevención."
    ]
    for f in focos:
        story.append(Paragraph(f, styles["Body"]))

    story.append(PageBreak())

    # --- Perfil del atleta ---
    story.append(Paragraph("Perfil del atleta", styles["H2"]))
    profile_lines = [
        f"Edad: {profile.get('age','N/A')} | Estatura: {profile.get('height_cm','N/A')} cm | Peso: {profile.get('weight_kg','N/A')} kg",
        f"Días running objetivo: {profile.get('days_run_per_week','N/A')} | Fuerza/semana: {profile.get('strength_days_per_week','N/A')} | App principal: {profile.get('main_app','N/A')}",
        f"Experiencia corriendo: {profile.get('running_experience','N/A')} | Km/semana (4w aprox): {profile.get('km_week_min','N/A')}-{profile.get('km_week_max','N/A')}",
        f"Superficie: {profile.get('surface','N/A')} | Otros deportes: {profile.get('other_sports','N/A')}",
        f"Ritmos reportados (Form): Suave {pace_str(profile.get('easy_pace_sec_per_km'))} | Moderado {pace_str(profile.get('mod_pace_sec_per_km'))} | Rápido {pace_str(profile.get('fast_pace_sec_per_km'))}"
    ]
    for ln in profile_lines:
        story.append(Paragraph(ln, styles["Body"]))

    story.append(Spacer(1, 0.15*inch))
    story.append(Paragraph("Check-in más reciente", styles["H2"]))
    if latest_checkin:
        ck = latest_checkin.get("latest_checkin", {})
        story.append(Paragraph(
            f"Sesiones completadas: {ck.get('sessions_completed','N/A')} | Sensación: {ck.get('feeling_1_10','N/A')}/10 | "
            f"Fatiga: {ck.get('fatigue_1_10','N/A')}/10 | Dolor: {ck.get('pain_0_10','N/A')}/10 ({ck.get('pain_where','N/A')})",
            styles["Body"]
        ))
        story.append(Paragraph(f"Sueño: {ck.get('sleep_text','N/A')} | Estrés: {ck.get('stress_text','N/A')} | Saltó sesiones: {ck.get('skipped_sessions','N/A')}", styles["Body"]))
        if ck.get("comments"):
            story.append(Paragraph(f"Comentarios: {ck.get('comments')}", styles["Body"]))
    else:
        story.append(Paragraph("No hay check-in registrado recientemente.", styles["Body"]))

    story.append(PageBreak())

    # --- Gráficos: volumen, ritmo, riesgo, zonas ---
    story.append(Paragraph("Tendencias y control de carga", styles["H2"]))
    story.append(Paragraph("Volumen semanal (km), ritmo promedio y métricas de control de carga para evitar picos.", styles["Body"]))

    for title, img in [
        ("Km por semana", km_chart),
        ("Ritmo promedio semanal", pace_chart),
        ("Riesgo de carga (ACWR/Monotonía)", load_chart),
        ("Zonas (aprox por ritmo promedio)", zones_chart),
    ]:
        if img.exists():
            story.append(Paragraph(title, styles["Body"]))
            story.append(Image(str(img), width=6.3*inch, height=3.2*inch))
            story.append(Spacer(1, 0.15*inch))

    story.append(PageBreak())

    # --- Plan semanal ---
    story.append(Paragraph("Plan semanal (running + fuerza)", styles["H2"]))
    story.append(Paragraph(
        "Este plan se ajusta automáticamente al estado del check-in y a las métricas de carga. "
        "Si aparece dolor o fatiga alta, se prioriza volumen fácil y recuperación.",
        styles["Body"]
    ))

    plan_rows = plan_to_table(plan.get("plan_by_day", {}))
    plan_table = Table(plan_rows, colWidths=[1.0*inch, 2.2*inch, 2.2*inch, 0.8*inch])
    plan_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.whitesmoke),
        ("BOX", (0,0), (-1,-1), 0.5, colors.black),
        ("INNERGRID", (0,0), (-1,-1), 0.25, colors.grey),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 10),
        ("FONTSIZE", (0,1), (-1,-1), 8),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("PADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(plan_table)

    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("<b>Notas clave</b>", styles["Body"]))
    for n in plan.get("notes", []):
        story.append(Paragraph(f"- {n}", styles["Body"]))

    story.append(PageBreak())

    # --- Fuerza (detalle) ---
    story.append(Paragraph("Fuerza y prevención", styles["H2"]))
    story.append(Paragraph(
        "La fuerza es parte del plan para mejorar economía de carrera y reducir riesgo de lesión. "
        "En semanas de descarga se reduce el volumen (series) para priorizar recuperación.",
        styles["Body"]
    ))
    story.append(Paragraph("Recomendación: 2 sesiones por semana (A/B) + movilidad 10 min post-entreno.", styles["Body"]))

    story.append(PageBreak())

    # --- Nutrición (guía general) ---
    story.append(Paragraph("Nutrición (guía general)", styles["H2"]))
    story.append(Paragraph(
        "Este bloque es una guía general para rendimiento. No reemplaza acompañamiento clínico. "
        "Foco: energía suficiente, proteínas consistentes, hidratación y práctica de alimentación en fondo.",
        styles["Body"]
    ))

    nutrition = [
        "Proteína en cada comida (pollo, huevos, atún, legumbres).",
        "Carbohidratos alrededor de entrenos (arroz, papa, pasta, avena).",
        "Hidratación diaria y electrolitos en fondos largos.",
        "Post-entreno: carbohidrato + proteína en 60–90 min.",
        "En fondos: practica geles o fuentes de carbos (si aplican)."
    ]
    for it in nutrition:
        story.append(Paragraph(f"- {it}", styles["Body"]))

    story.append(PageBreak())

    # --- Cierre ---
    story.append(Paragraph("Cierre y próximos pasos", styles["H2"]))
    story.append(Paragraph(
        "1) Cumple el plan lo más consistente posible.\n"
        "2) Si dolor sube (≥4) o fatiga muy alta (≥8), ajustamos a semana de descarga.\n"
        "3) Llena el check-in semanal para mantener el plan alineado con tu estado real.",
        styles["Body"]
    ))
    story.append(Paragraph("Fin del reporte.", styles["Center"]))

    doc.build(story)
    print(f"✅ PDF generado: {out_pdf}")


def main(cedula: str | None = None):
    import argparse
    load_dotenv()
    if cedula is None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--cedula", required=True)
        args = parser.parse_args()
        cedula = args.cedula
    generate_pdf(cedula)


if __name__ == "__main__":
    main()