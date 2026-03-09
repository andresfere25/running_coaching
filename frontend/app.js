/**
 * app.js — Lógica del dashboard de Running Coaching.
 *
 * Usa Alpine.js (declarativo, reactivo, sin build).
 * Los datos vienen del backend FastAPI en /athletes/...
 */

const API_BASE = '';  // mismo origen: frontend y backend en localhost:8000

async function apiFetch(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Error ${res.status}: ${res.statusText}`);
  }
  return res.json();
}

// ─── Colores del semáforo ──────────────────────────────────────────────────
const SEM_CONFIG = {
  VERDE:       { bg: '#16a34a', badge: '#dcfce7', text: 'text-green-800',  label: 'Estado óptimo' },
  AMARILLO:    { bg: '#ca8a04', badge: '#fef9c3', text: 'text-yellow-800', label: 'Precaución' },
  ROJO:        { bg: '#dc2626', badge: '#fee2e2', text: 'text-red-800',    label: 'Reducir carga' },
  SIN_CHECKIN: { bg: '#6b7280', badge: '#f3f4f6', text: 'text-gray-700',  label: 'Sin check-in reciente' },
};

// ─── Formateo ──────────────────────────────────────────────────────────────
function fmt(v, dec = 1) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return Number(v).toFixed(dec);
}

function paceStr(secPerKm) {
  if (!secPerKm || isNaN(secPerKm)) return '—';
  const m = Math.floor(secPerKm / 60);
  const s = Math.round(secPerKm % 60);
  return `${m}:${String(s).padStart(2, '0')} /km`;
}

/** Convierte segundos totales a H:MM:SS o MM:SS según magnitud */
function secToRaceTime(sec) {
  if (!sec || isNaN(sec)) return '—';
  const s = Math.round(Number(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
  return `${m}:${String(ss).padStart(2, '0')}`;
}

/** Convierte segundos de diferencia a string legible como "+5 min 30 seg" */
function secToDiffStr(sec) {
  if (!sec || isNaN(sec)) return null;
  const abs = Math.abs(Math.round(sec));
  const m = Math.floor(abs / 60);
  const s = abs % 60;
  const sign = sec > 0 ? '+' : '-';
  if (m === 0) return `${sign}${s} seg`;
  if (s === 0) return `${sign}${m} min`;
  return `${sign}${m} min ${s} seg`;
}

function shortDate(dateStr) {
  if (!dateStr) return '';
  return dateStr.slice(5); // YYYY-MM-DD → MM-DD
}

// ─── Orden de días ─────────────────────────────────────────────────────────
const WEEK_DAYS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];

// ─── PR por distancia ──────────────────────────────────────────────────────
const PR_KEY_MAP = {
  '5K': 'pr_5k_sec', '10K': 'pr_10k_sec', '21K': 'pr_21k_sec', '42K': 'pr_42k_sec',
};

// ─── Instancias de Chart.js (para destroy/recreate) ───────────────────────
let chartKm   = null;
let chartAcwr = null;

// ─── Componente principal Alpine.js ───────────────────────────────────────
function athleteApp() {
  return {
    // Estado
    cedula:   '1070982737',
    loading:  false,
    error:    null,
    snapshot: null,
    plan:     null,
    features: null,

    // ── Inicialización ────────────────────────────────────────────────────
    init() {
      this.search();
    },

    // ── Buscar datos del atleta ───────────────────────────────────────────
    async search() {
      if (!this.cedula.trim()) return;
      this.loading  = true;
      this.error    = null;
      this.snapshot = null;
      this.plan     = null;
      this.features = null;

      try {
        const [snapshot, plan, features] = await Promise.all([
          apiFetch(`/athletes/${this.cedula}/snapshot`),
          apiFetch(`/athletes/${this.cedula}/plan`),
          apiFetch(`/athletes/${this.cedula}/features?weeks=12`),
        ]);
        this.snapshot = snapshot;
        this.plan     = plan;
        this.features = features;

        this.$nextTick(() => this._renderCharts());

      } catch (e) {
        this.error = e.message;
      } finally {
        this.loading = false;
      }
    },

    // ── Semáforo ──────────────────────────────────────────────────────────
    get semConfig() {
      const key = this.snapshot?.semaforo_latest_checkin || 'SIN_CHECKIN';
      return SEM_CONFIG[key] || SEM_CONFIG['SIN_CHECKIN'];
    },

    get semLabel() {
      const k = this.snapshot?.semaforo_latest_checkin || 'SIN_CHECKIN';
      return k.replace('_', ' ');
    },

    // ── Objetivo y perfil ─────────────────────────────────────────────────
    get athleteName() {
      return this.snapshot?.profile?.name || '—';
    },

    get profile() {
      return this.snapshot?.profile || {};
    },

    /**
     * Datos del bloque de objetivo de carrera:
     * - raceDistance, raceDate, countdownDays
     * - timeGoalFormatted (nuevo campo del snapshot)
     * - prFormatted: PR en la distancia objetivo
     * - gapSec: diferencia PR - meta (positivo = hay que mejorar)
     */
    get goalInfo() {
      const p  = this.snapshot?.profile || {};
      const sn = this.snapshot || {};

      const raceDistance = p.race_distance || null;
      const prKey        = PR_KEY_MAP[raceDistance];
      const prSec        = prKey ? p[prKey] : null;
      const goalSec      = p.time_goal_sec;

      const gapSec = (prSec && goalSec && prSec > 0 && goalSec > 0)
        ? prSec - goalSec
        : null;

      return {
        raceDistance,
        raceDate:          p.race_date_raw || '—',
        countdownDays:     sn.race_countdown_days,
        timeGoalFormatted: sn.time_goal_formatted || secToRaceTime(goalSec),
        prFormatted:       prSec ? secToRaceTime(prSec) : null,
        gapSec,
        gapStr:            secToDiffStr(gapSec),
        // La meta es más rápida que el PR → hay trabajo por hacer
        needsImprovement:  gapSec !== null && gapSec > 0,
        // Ya superó la meta con el PR actual
        alreadyOnTarget:   gapSec !== null && gapSec <= 0,
      };
    },

    // ── KPIs de la última semana ──────────────────────────────────────────
    get kpis() {
      const w = this.snapshot?.latest_week || {};
      return [
        { label: 'Km semana',  value: fmt(w.km_week),                    unit: 'km',  color: 'blue'   },
        { label: 'Sesiones',   value: fmt(w.sessions_week, 0),           unit: 'ses', color: 'indigo' },
        { label: 'ACWR',       value: fmt(w.acwr, 2),                    unit: '',    color: acwrColor(w.acwr) },
        { label: 'Monotonía',  value: fmt(w.monotony, 2),                unit: '',    color: 'purple' },
        { label: 'Km fondo',   value: fmt(w.long_run_km),                unit: 'km',  color: 'cyan'   },
        { label: 'Ritmo prom', value: paceStr(w.pace_sec_per_km_week),   unit: '',    color: 'teal'   },
      ];
    },

    get dataWeeksAvailable() {
      return this.snapshot?.data_weeks_available ?? null;
    },

    get needsMoreData() {
      const weeks = this.dataWeeksAvailable;
      return weeks !== null && weeks < 4;
    },

    // ── Resumen del coach ─────────────────────────────────────────────────
    get coachSummary() {
      if (!this.plan) return null;
      const exp = this.plan.coach_explanation || {};
      return {
        weekSummary:       this.plan.week_summary || '',
        weeklyFocus:       this.plan.weekly_focus || '',
        trigger:           exp.trigger || '',
        baseKm:            exp.base_km,
        multiplier:        exp.multiplier,
        targetKm:          exp.target_km,
        nextIfVerdeKm:     exp.next_week_if_verde_km,
        kmWeekMinProfile:  exp.km_week_min_profile,
        kmWeekMaxProfile:  exp.km_week_max_profile,
      };
    },

    // ── Distribución de km ────────────────────────────────────────────────
    get distributionBars() {
      const d = this.plan?.distribution_km;
      if (!d) return [];
      const total = (d.easy_km || 0) + (d.fondo_km || 0) + (d.quality_km || 0);
      if (total === 0) return [];
      return [
        {
          label: 'Suaves',
          km:    d.easy_km || 0,
          pct:   Math.round((d.easy_km || 0) / total * 100),
          color: '#22c55e',
          bg:    '#f0fdf4',
          text:  '#15803d',
        },
        {
          label: 'Fondo',
          km:    d.fondo_km || 0,
          pct:   Math.round((d.fondo_km || 0) / total * 100),
          color: '#3b82f6',
          bg:    '#eff6ff',
          text:  '#1d4ed8',
        },
        {
          label: 'Calidad',
          km:    d.quality_km || 0,
          pct:   Math.round((d.quality_km || 0) / total * 100),
          color: '#f97316',
          bg:    '#fff7ed',
          text:  '#c2410c',
        },
      ];
    },

    // ── Plan semanal ──────────────────────────────────────────────────────
    get planDays() {
      if (!this.plan?.plan_by_day) return [];
      return WEEK_DAYS.map(day => ({
        day,
        sessions: this.plan.plan_by_day[day] || [],
      }));
    },

    get weekType() {
      return this.plan?.week_type || '—';
    },

    get weekTypeColor() {
      return { PROGRESO: '#16a34a', CONSERVADORA: '#ca8a04', DESCARGA: '#dc2626' }[this.weekType] || '#6b7280';
    },

    get weekTypeBg() {
      return { PROGRESO: '#f0fdf4', CONSERVADORA: '#fefce8', DESCARGA: '#fef2f2' }[this.weekType] || '#f9fafb';
    },

    get planNotes() {
      return this.plan?.notes || [];
    },

    // ── Gráficos Chart.js ─────────────────────────────────────────────────
    _renderCharts() {
      if (!this.features?.data?.length) return;

      const data   = this.features.data;
      const labels = data.map(d => shortDate(d.week_start));
      const kms    = data.map(d => d.km_week ?? null);
      const acwrs  = data.map(d => d.acwr ?? null);

      if (chartKm)   { chartKm.destroy();   chartKm   = null; }
      if (chartAcwr) { chartAcwr.destroy(); chartAcwr = null; }

      const kmCtx = document.getElementById('chartKm');
      if (kmCtx) {
        chartKm = new Chart(kmCtx, {
          type: 'bar',
          data: {
            labels,
            datasets: [{
              label: 'Km / semana',
              data: kms,
              backgroundColor: 'rgba(59, 130, 246, 0.7)',
              borderColor:     'rgba(59, 130, 246, 1)',
              borderWidth: 1,
              borderRadius: 4,
            }],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
              y: { beginAtZero: true, grid: { color: '#f3f4f6' } },
              x: { grid: { display: false } },
            },
          },
        });
      }

      const acwrCtx = document.getElementById('chartAcwr');
      if (acwrCtx) {
        chartAcwr = new Chart(acwrCtx, {
          type: 'line',
          data: {
            labels,
            datasets: [{
              label: 'ACWR',
              data: acwrs,
              borderColor:     'rgba(139, 92, 246, 1)',
              backgroundColor: 'rgba(139, 92, 246, 0.1)',
              borderWidth: 2,
              pointRadius: 4,
              tension: 0.3,
              fill: true,
            }],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { display: false },
              annotation: {},
            },
            scales: {
              y: {
                min: 0, max: 2,
                grid: { color: '#f3f4f6' },
                ticks: { stepSize: 0.5 },
              },
              x: { grid: { display: false } },
            },
          },
          plugins: [{
            beforeDraw(chart) {
              const { ctx, chartArea: { left, right }, scales: { y } } = chart;
              if (!y) return;
              const y08 = y.getPixelForValue(0.8);
              const y13 = y.getPixelForValue(1.3);

              ctx.save();
              ctx.fillStyle = 'rgba(22, 163, 74, 0.08)';
              ctx.fillRect(left, y13, right - left, y08 - y13);

              ctx.strokeStyle = 'rgba(22, 163, 74, 0.5)';
              ctx.lineWidth = 1;
              ctx.setLineDash([4, 4]);
              [y08, y13].forEach(yv => {
                ctx.beginPath(); ctx.moveTo(left, yv); ctx.lineTo(right, yv); ctx.stroke();
              });
              ctx.restore();
            },
          }],
        });
      }
    },

    // ── Utilidades para la plantilla ──────────────────────────────────────
    sessionColor(type) {
      return type === 'Running' ? '#3b82f6' : '#f97316';
    },

    sessionBg(type) {
      return type === 'Running' ? '#eff6ff' : '#fff7ed';
    },

    kpiCardStyle(color) {
      const map = {
        blue:   { bg: '#eff6ff', text: '#1d4ed8' },
        indigo: { bg: '#eef2ff', text: '#4338ca' },
        green:  { bg: '#f0fdf4', text: '#15803d' },
        yellow: { bg: '#fefce8', text: '#a16207' },
        red:    { bg: '#fef2f2', text: '#b91c1c' },
        purple: { bg: '#faf5ff', text: '#7e22ce' },
        cyan:   { bg: '#ecfeff', text: '#0e7490' },
        teal:   { bg: '#f0fdfa', text: '#0f766e' },
        gray:   { bg: '#f9fafb', text: '#374151' },
      };
      return map[color] || map.gray;
    },

    multiplierLabel(m) {
      if (!m) return '';
      if (m < 1) return `×${m.toFixed(2)} (reducción)`;
      if (m > 1) return `×${m.toFixed(2)} (aumento)`;
      return `×${m.toFixed(2)}`;
    },
  };
}

function acwrColor(v) {
  if (v === null || v === undefined || isNaN(v)) return 'gray';
  if (v < 0.8 || v > 1.5) return 'red';
  if (v > 1.3) return 'yellow';
  return 'green';
}
