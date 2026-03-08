/**
 * app.js — Lógica del dashboard de Running Coaching.
 *
 * Usa Alpine.js (declarativo, reactivo, sin build).
 * Los datos vienen del backend FastAPI en /athletes/...
 *
 * Separar la lógica del HTML facilita la futura migración a React/Next.js.
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

function shortDate(dateStr) {
  if (!dateStr) return '';
  return dateStr.slice(5); // YYYY-MM-DD → MM-DD
}

// ─── Orden de días ─────────────────────────────────────────────────────────
const WEEK_DAYS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];

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

        // Esperar a que Alpine renderice el DOM antes de pintar charts
        this.$nextTick(() => this._renderCharts());

      } catch (e) {
        this.error = e.message;
      } finally {
        this.loading = false;
      }
    },

    // ── Datos computados ─────────────────────────────────────────────────

    get semConfig() {
      const key = this.snapshot?.semaforo_latest_checkin || 'SIN_CHECKIN';
      return SEM_CONFIG[key] || SEM_CONFIG['SIN_CHECKIN'];
    },

    get semLabel() {
      const k = this.snapshot?.semaforo_latest_checkin || 'SIN_CHECKIN';
      return k.replace('_', ' ');
    },

    get athleteName() {
      return this.snapshot?.profile?.name || '—';
    },

    get kpis() {
      const w = this.snapshot?.latest_week || {};
      const p = this.snapshot?.profile    || {};
      return [
        { label: 'Km semana',  value: fmt(w.km_week),        unit: 'km',       color: 'blue'   },
        { label: 'Sesiones',   value: fmt(w.sessions_week,0), unit: 'ses',      color: 'indigo' },
        { label: 'ACWR',       value: fmt(w.acwr, 2),         unit: '',         color: acwrColor(w.acwr) },
        { label: 'Monotonía',  value: fmt(w.monotony, 2),     unit: '',         color: 'purple' },
        { label: 'Km fondo',   value: fmt(w.long_run_km),     unit: 'km',       color: 'cyan'   },
        { label: 'Ritmo prom', value: paceStr(w.pace_sec_per_km_week), unit: '', color: 'teal'  },
      ];
    },

    get profile() {
      return this.snapshot?.profile || {};
    },

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

      // Destruir instancias anteriores
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
            datasets: [
              {
                label: 'ACWR',
                data: acwrs,
                borderColor:     'rgba(139, 92, 246, 1)',
                backgroundColor: 'rgba(139, 92, 246, 0.1)',
                borderWidth: 2,
                pointRadius: 4,
                tension: 0.3,
                fill: true,
              },
            ],
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
            // Dibuja las bandas de referencia del ACWR manualmente
            beforeDraw(chart) {
              const { ctx, chartArea: { left, right, top, bottom }, scales: { y } } = chart;
              if (!y) return;
              const y08 = y.getPixelForValue(0.8);
              const y13 = y.getPixelForValue(1.3);

              // Banda verde (zona segura 0.8–1.3)
              ctx.save();
              ctx.fillStyle = 'rgba(22, 163, 74, 0.08)';
              ctx.fillRect(left, y13, right - left, y08 - y13);

              // Líneas de referencia
              ctx.strokeStyle = 'rgba(22, 163, 74, 0.5)';
              ctx.lineWidth = 1;
              ctx.setLineDash([4, 4]);
              [y08, y13].forEach(y => {
                ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
              });
              ctx.restore();
            },
          }],
        });
      }
    },

    // ── Utilidades para la plantilla ─────────────────────────────────────
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
  };
}

function acwrColor(v) {
  if (v === null || v === undefined || isNaN(v)) return 'gray';
  if (v < 0.8 || v > 1.5) return 'red';
  if (v > 1.3) return 'yellow';
  return 'green';
}
