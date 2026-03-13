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
  VERDE:       { bg: '#059669', badge: '#d1fae5', text: 'text-emerald-800', label: 'Estado óptimo' },
  AMARILLO:    { bg: '#d97706', badge: '#fef3c7', text: 'text-amber-800',   label: 'Precaución' },
  ROJO:        { bg: '#dc2626', badge: '#fee2e2', text: 'text-red-800',     label: 'Reducir carga' },
  SIN_CHECKIN: { bg: '#334155', badge: '#f1f5f9', text: 'text-slate-700',  label: 'Sin check-in reciente' },
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

/** Convierte "2026-02-24" → "Sem. 09 · 24 feb" */
function weekLabel(dateStr) {
  if (!dateStr) return '—';
  const d = new Date(dateStr + 'T00:00:00');
  const jan4 = new Date(d.getFullYear(), 0, 4);
  const startOfW1 = new Date(jan4);
  startOfW1.setDate(jan4.getDate() - ((jan4.getDay() + 6) % 7));
  const weekNum = Math.floor((d - startOfW1) / (7 * 24 * 3600 * 1000)) + 1;
  const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
  return `Sem. ${String(weekNum).padStart(2,'0')} · ${d.getDate()} ${months[d.getMonth()]}`;
}

/** Versión compacta para ejes de gráficos: "S09 · 24feb" */
function weekLabelChart(dateStr) {
  if (!dateStr) return '—';
  const d = new Date(dateStr + 'T00:00:00');
  const jan4 = new Date(d.getFullYear(), 0, 4);
  const startOfW1 = new Date(jan4);
  startOfW1.setDate(jan4.getDate() - ((jan4.getDay() + 6) % 7));
  const weekNum = Math.floor((d - startOfW1) / (7 * 24 * 3600 * 1000)) + 1;
  const months = ['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
  return `S${String(weekNum).padStart(2,'0')} · ${d.getDate()}${months[d.getMonth()]}`;
}

// ─── Orden de días ─────────────────────────────────────────────────────────
const WEEK_DAYS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'];

// ─── Mapa nombre → clave canónica de día ───────────────────────────────────
const DAY_KEY_MAP = {
  'Lunes': 'LUN', 'Martes': 'MAR', 'Miércoles': 'MIE',
  'Jueves': 'JUE', 'Viernes': 'VIE', 'Sábado': 'SAB', 'Domingo': 'DOM',
};

// ─── PR por distancia ──────────────────────────────────────────────────────
const PR_KEY_MAP = {
  '5K': 'pr_5k_sec', '10K': 'pr_10k_sec', '21K': 'pr_21k_sec', '42K': 'pr_42k_sec',
};

// ─── ACWR zona → label y color ────────────────────────────────────────────
const ACWR_ZONE_CONFIG = {
  BAJO:       { label: 'BAJO',       bg: '#eff6ff', text: '#1d4ed8', badge: 'bg-blue-100 text-blue-700',   desc: 'Subentrenamiento' },
  OPTIMO:     { label: 'ÓPTIMO',     bg: '#f0fdf4', text: '#15803d', badge: 'bg-green-100 text-green-700', desc: 'Rango seguro' },
  PRECAUCION: { label: 'PRECAUCIÓN', bg: '#fefce8', text: '#a16207', badge: 'bg-yellow-100 text-yellow-700', desc: 'Monitorear carga' },
  ALTO:       { label: 'ALTO',       bg: '#fef2f2', text: '#b91c1c', badge: 'bg-red-100 text-red-700',     desc: 'Riesgo elevado' },
  SIN_DATOS:  { label: 'SIN DATOS',  bg: '#f9fafb', text: '#6b7280', badge: 'bg-gray-100 text-gray-500',   desc: 'Sin historial' },
};

// ─── Instancias de Chart.js (para destroy/recreate) ───────────────────────
let chartKm   = null;
let chartAcwr = null;
let chartCtl  = null;

// ─── Componente principal Alpine.js ───────────────────────────────────────
function athleteApp() {
  return {
    // Estado
    cedula:       '1070982737',
    activeTab:    'hoy',
    loading:      false,
    error:        null,
    snapshot:     null,
    plan:         null,
    features:     null,
    prediction:   null,
    predTarget:   '42K',
    coachContent: null,
    activities:   null,
    activitiesLoading: false,
    checkin:      null,

    // ── Inicialización ────────────────────────────────────────────────────
    init() {
      this.search();
    },

    // ── Buscar datos del atleta ───────────────────────────────────────────
    async search() {
      if (!this.cedula.trim()) return;
      this.loading    = true;
      this.error      = null;
      this.snapshot     = null;
      this.plan         = null;
      this.features     = null;
      this.prediction   = null;
      this.coachContent = null;
      this.activities   = null;
      this.checkin      = null;

      try {
        const [snapshot, plan, features] = await Promise.all([
          apiFetch(`/athletes/${this.cedula}/snapshot`),
          apiFetch(`/athletes/${this.cedula}/plan`),
          apiFetch(`/athletes/${this.cedula}/features?weeks=12`),
        ]);
        this.snapshot = snapshot;
        this.plan     = plan;
        this.features = features;

        // Usar la distancia objetivo del atleta como target por defecto
        if (snapshot?.profile?.race_distance) {
          this.predTarget = snapshot.profile.race_distance;
        }

        this.$nextTick(() => this._renderCharts());

        // Cargar predicción, coach, actividades y checkin en paralelo
        this.fetchPrediction();
        this.fetchCoachContent();
        this.fetchActivities();
        this.fetchCheckin();

      } catch (e) {
        this.error = e.message;
      } finally {
        this.loading = false;
      }
    },

    // ── Contenido editorial del coach ─────────────────────────────────────
    async fetchCoachContent() {
      try {
        this.coachContent = await apiFetch(`/athletes/${this.cedula}/coach-content`);
      } catch (e) {
        this.coachContent = { source: 'auto_fallback' };
      }
    },

    // Nota del coach para un día dado (nombre completo → clave canónica → nota)
    sessionNoteFor(dayName) {
      const notes = this.coachContent?.session_notes || {};
      const key = DAY_KEY_MAP[dayName] || dayName;
      return notes[key] || null;
    },

    // ¿Hay contenido publicado activo?
    get hasPublishedCoach() {
      return this.coachContent?.source === 'published';
    },

    // ── Actividades Strava ────────────────────────────────────────────────
    async fetchActivities() {
      this.activitiesLoading = true;
      try {
        // Sin filtro de sport_type: traemos todas para separar en getters
        this.activities = await apiFetch(
          `/athletes/${this.cedula}/activities?limit=30`
        );
      } catch (e) {
        this.activities = { data: [], count: 0, error: e.message };
      } finally {
        this.activitiesLoading = false;
      }
    },

    get recentRuns() {
      return (this.activities?.data || []).filter(
        a => (a.sport_type || '').toLowerCase().includes('run')
      );
    },

    get recentCross() {
      return (this.activities?.data || []).filter(
        a => !(a.sport_type || '').toLowerCase().includes('run')
      ).slice(0, 8);
    },

    /** Icono emoji según tipo de actividad */
    crossTypeIcon(sport_type) {
      const t = (sport_type || '').toLowerCase();
      if (t.includes('ride') || t.includes('cycl') || t.includes('bik')) return '🚴';
      if (t.includes('swim'))                                              return '🏊';
      if (t.includes('yoga'))                                              return '🧘';
      if (t.includes('walk') || t.includes('hike'))                       return '🥾';
      if (t.includes('weight') || t.includes('strength') || t.includes('workout')) return '💪';
      if (t.includes('soccer') || t.includes('football'))                 return '⚽';
      if (t.includes('tennis'))                                            return '🎾';
      if (t.includes('ski'))                                               return '⛷️';
      return '⚡';
    },

    /** Clases Tailwind para el badge de tipo */
    crossTypeBadge(sport_type) {
      const t = (sport_type || '').toLowerCase();
      if (t.includes('ride') || t.includes('cycl') || t.includes('bik')) return 'bg-blue-100 text-blue-700';
      if (t.includes('swim'))                                              return 'bg-cyan-100 text-cyan-700';
      if (t.includes('yoga'))                                              return 'bg-green-100 text-green-700';
      if (t.includes('weight') || t.includes('strength') || t.includes('workout')) return 'bg-indigo-100 text-indigo-700';
      if (t.includes('walk') || t.includes('hike'))                       return 'bg-amber-100 text-amber-700';
      return 'bg-gray-100 text-gray-600';
    },

    /** Formatea segundos como "1h 23min" o "45 min" */
    fmtDuration(sec) {
      if (!sec || isNaN(sec)) return '—';
      const s = Math.round(Number(sec));
      const h = Math.floor(s / 3600);
      const m = Math.floor((s % 3600) / 60);
      if (h > 0) return `${h}h ${m}min`;
      return `${m} min`;
    },

    // ── Último check-in ───────────────────────────────────────────────────
    async fetchCheckin() {
      try {
        this.checkin = await apiFetch(`/athletes/${this.cedula}/checkin`);
      } catch (e) {
        this.checkin = null;
      }
    },

    // ── Predicción de rendimiento ─────────────────────────────────────────
    async fetchPrediction() {
      this.prediction = null;
      try {
        this.prediction = await apiFetch(
          `/athletes/${this.cedula}/prediction?target=${this.predTarget}`
        );
      } catch (e) {
        this.prediction = { error: 'FETCH_ERROR', message: e.message };
      }
    },

    // Colores del badge de confianza
    confidenceBadgeClass(c) {
      return {
        ALTA:       'bg-green-100 text-green-700',
        MEDIA:      'bg-blue-100 text-blue-700',
        'MEDIA-BAJA': 'bg-yellow-100 text-yellow-700',
        BAJA:       'bg-orange-100 text-orange-700',
      }[c] || 'bg-gray-100 text-gray-600';
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
        { label: 'Km semana',      value: fmt(w.km_week),                  unit: 'km',  color: 'blue'   },
        { label: 'Sesiones',       value: fmt(w.sessions_week, 0),         unit: 'ses', color: 'indigo' },
        { label: 'ACWR (EWMA)',    value: fmt(w.acwr, 2),                  unit: '',    color: acwrColor(w.acwr) },
        { label: 'Monotonía',      value: fmt(w.monotony, 2),              unit: '',    color: 'purple' },
        { label: 'Fondo / mes',    value: fmt(w.fondo_largo_4s),           unit: 'km',  color: 'cyan'   },
        { label: 'Racha activa',   value: fmt(w.racha_semanas, 0),        unit: 'sem', color: 'green'  },
        { label: 'Ritmo prom',     value: paceStr(w.pace_sec_per_km_week), unit: '',    color: 'teal'   },
        { label: 'Fondo semana',   value: fmt(w.long_run_km),              unit: 'km',  color: 'gray'   },
      ];
    },

    // ── Panel de forma (CTL / ATL / TSB / Readiness) ─────────────────────
    get formaPanel() {
      const w   = this.snapshot?.latest_week || {};
      const sn  = this.snapshot || {};
      const ctl = w.ctl  ?? null;
      const atl = w.atl  ?? null;
      const tsb = w.tsb  ?? null;
      const rs  = sn.readiness_score ?? null;
      const zone = sn.acwr_zone_latest || 'SIN_DATOS';
      const zoneCfg = ACWR_ZONE_CONFIG[zone] || ACWR_ZONE_CONFIG.SIN_DATOS;

      // Color de TSB: positivo=fresco, cerca de 0=en forma, negativo=fatigado
      const tsbColor = tsb === null ? 'gray'
        : tsb > 10  ? 'green'
        : tsb >= 0  ? 'blue'
        : tsb >= -15 ? 'yellow'
        : 'red';

      // Readiness: color según rango
      const rsColor = rs === null ? 'gray'
        : rs >= 75 ? 'green'
        : rs >= 55 ? 'blue'
        : rs >= 40 ? 'yellow'
        : 'red';

      // Tendencia de ritmo (pace_delta_4s_sec): negativo = más rápido
      const paceDelta = w.pace_delta_4s_sec ?? null;
      const paceTrendLabel = paceDelta === null ? null
        : paceDelta < -5  ? '↓ más rápido vs. prom. 4 sem.'
        : paceDelta > 5   ? '↑ más lento vs. prom. 4 sem.'
        : '→ estable vs. prom. 4 sem.';
      const paceTrendColor = paceDelta === null ? 'gray'
        : paceDelta < -5  ? 'green'
        : paceDelta > 5   ? 'red'
        : 'blue';

      // Tendencia de volumen
      const kmTrend = w.km_trend ?? null;
      const kmTrendLabel = { growing: '↑ creciendo', stable: '→ estable', declining: '↓ reduciendo' }[kmTrend] || null;
      const kmTrendColor = { growing: 'green', stable: 'blue', declining: 'yellow' }[kmTrend] || 'gray';

      return {
        ctl: fmt(ctl, 1), atl: fmt(atl, 1), tsb: fmt(tsb, 1), tsbColor,
        readinessScore: rs, rsColor,
        acwrZone: zone, acwrZoneLabel: zoneCfg.label,
        acwrZoneBadge: zoneCfg.badge, acwrZoneDesc: zoneCfg.desc,
        spikeAlert: w.semana_spike === true || w.semana_spike === 'true',
        picoRatio: w.pico_semana_ratio != null ? fmt(w.pico_semana_ratio * 100, 0) + '%' : '—',
        paceTrendLabel, paceTrendColor,
        kmTrendLabel, kmTrendColor,
        hasData: ctl !== null,
      };
    },

    get dataWeeksAvailable() {
      return this.snapshot?.data_weeks_available ?? null;
    },

    get needsMoreData() {
      const weeks = this.dataWeeksAvailable;
      return weeks !== null && weeks < 4;
    },

    // ── weekly_focus efectivo: editorial si existe, automático como fallback ─
    get effectiveWeeklyFocus() {
      if (this.coachContent?.source === 'published' && this.coachContent?.weekly_focus) {
        return this.coachContent.weekly_focus;
      }
      return this.plan?.weekly_focus || '';
    },

    // ── Override del coach para un día (clave canónica) ───────────────────
    planOverrideFor(dayName) {
      const overrides = this.coachContent?.plan_overrides || {};
      const key = DAY_KEY_MAP[dayName] || dayName;
      return overrides[key] || null;
    },

    /**
     * Aplica el override del coach a un array de sessions.
     * override = { type?, title?, km?, pace?, note? }
     *
     * Regla:
     *   - Si override.type o override.title → REEMPLAZO COMPLETO:
     *       descarta las sesiones base, construye 1 nueva sesión del coach.
     *       km/pace son opcionales (fallback al base si no se especifican).
     *   - Si solo km/pace → PARCHE PARCIAL al primer Running (o primero).
     *   - Añade _coachOverride=true y _coachReplace=true según el caso.
     */
    _applyOverride(sessions, override) {
      if (!override) return sessions;

      // ── Reemplazo completo ─────────────────────────────────────────────
      if (override.type || override.title) {
        const base = sessions[0] || {};
        return [{
          type:    override.type  || base.type    || 'Running',
          session: override.title || base.session || 'Sesión del coach',
          km:      override.km   != null ? String(override.km)   : (base.km   || null),
          pace:    override.pace != null ? String(override.pace) : (base.pace || null),
          details: [],
          _coachOverride: true,
          _coachReplace:  true,
        }];
      }

      // ── Parche parcial (solo km/pace) ──────────────────────────────────
      if (!override.km && !override.pace) return sessions;
      let applied = false;
      const hasRun = sessions.some(s => s.type === 'Running');
      return sessions.map(s => {
        const isTarget = !applied && (s.type === 'Running' || !hasRun);
        if (!isTarget) return s;
        applied = true;
        return {
          ...s,
          km:   override.km   != null ? String(override.km)   : s.km,
          pace: override.pace != null ? String(override.pace) : s.pace,
          _coachOverride: true,
        };
      });
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

    // ── Frase humana del cálculo de km (reemplaza "Base × factor → target") ─
    get coachCalcHuman() {
      const cs = this.coachSummary;
      if (!cs || !cs.baseKm) return null;
      const base = cs.baseKm.toFixed(1);
      const target = this.plan?.targets?.target_km_week ?? '—';
      const next = cs.nextIfVerdeKm;
      let phrase = `Partiendo de tu semana de ${base} km, esta semana hacemos ${target} km.`;
      if (next) phrase += ` Si check-in VERDE → ~${next} km la próxima.`;
      return phrase;
    },

    // ── Primera frase del week_summary (para el Hero) ────────────────────
    get shortSummary() {
      const s = this.plan?.week_summary || '';
      if (!s) return '';
      const dot = s.indexOf('.');
      if (dot > 0 && dot < 110) return s.slice(0, dot + 1);
      return s.length > 110 ? s.slice(0, 107) + '…' : s;
    },

    // ── Sesión correspondiente al día actual (con override del coach aplicado) ─
    get todaySession() {
      if (!this.plan?.plan_by_day) return { day: '—', sessions: [], override: null, note: null };
      const days = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];
      const today = days[new Date().getDay()];
      const base  = this.plan.plan_by_day[today] || [];
      const dayKey = DAY_KEY_MAP[today] || today;
      const override = (this.coachContent?.plan_overrides || {})[dayKey] || null;
      const note     = (this.coachContent?.session_notes   || {})[dayKey] || null;
      return {
        day: today,
        sessions: this._applyOverride(base, override),
        override,
        note,
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

    // ── Plan semanal (con overrides del coach aplicados) ─────────────────
    get planDays() {
      if (!this.plan?.plan_by_day) return [];
      return WEEK_DAYS.map(day => {
        const base    = this.plan.plan_by_day[day] || [];
        const dayKey  = DAY_KEY_MAP[day] || day;
        const override = (this.coachContent?.plan_overrides || {})[dayKey] || null;
        return {
          day,
          sessions: this._applyOverride(base, override),
          override,
        };
      });
    },

    // ── Km objetivo semanal efectivo (recalcula si hay overrides de km) ──
    get effectiveTargetKm() {
      const overrides = this.coachContent?.plan_overrides || {};
      const hasKmOverride = Object.values(overrides).some(o => o?.km != null);
      if (!hasKmOverride) return this.plan?.targets?.target_km_week ?? null;

      if (!this.plan?.plan_by_day) return this.plan?.targets?.target_km_week ?? null;
      let total = 0;
      for (const day of WEEK_DAYS) {
        const dayKey   = DAY_KEY_MAP[day];
        const override = overrides[dayKey];
        const sessions = this.plan.plan_by_day[day] || [];
        const runSessions = sessions.filter(s => s.type === 'Running');
        if (runSessions.length > 0) {
          // Primer Running: usar override.km si existe
          const baseKm = Number(runSessions[0].km) || 0;
          total += override?.km != null ? Number(override.km) : baseKm;
          // Demás Running del mismo día: siempre base
          for (let i = 1; i < runSessions.length; i++) {
            total += Number(runSessions[i].km) || 0;
          }
        }
      }
      return total > 0 ? Math.round(total * 10) / 10 : this.plan?.targets?.target_km_week ?? null;
    },

    // ── Día con la sesión de running más larga (sesión clave) ─────────────
    get keyDay() {
      let max = 0, key = null;
      for (const d of this.planDays) {
        for (const s of d.sessions) {
          if (s.type === 'Running' && s.km && Number(s.km) > max) {
            max = Number(s.km);
            key = d.day;
          }
        }
      }
      return key;
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
      const labels = data.map(d => weekLabelChart(d.week_start));
      const kms    = data.map(d => d.km_week ?? null);
      const acwrs  = data.map(d => d.acwr ?? null);
      const ctls   = data.map(d => d.ctl  ?? null);
      const atls   = data.map(d => d.atl  ?? null);

      if (chartKm)   { chartKm.destroy();   chartKm   = null; }
      if (chartAcwr) { chartAcwr.destroy(); chartAcwr = null; }
      if (chartCtl)  { chartCtl.destroy();  chartCtl  = null; }

      const kmCtx = document.getElementById('chartKm');
      if (kmCtx) {
        chartKm = new Chart(kmCtx, {
          type: 'bar',
          data: {
            labels,
            datasets: [{
              label: 'Km / semana',
              data: kms,
              backgroundColor: 'rgba(234, 88, 12, 0.75)',
              borderColor:     'rgba(234, 88, 12, 1)',
              borderWidth: 1,
              borderRadius: 4,
            }],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
              y: { beginAtZero: true, grid: { color: '#f1f5f9' }, ticks: { color: '#64748b', font: { size: 11 } } },
              x: { grid: { display: false }, ticks: { color: '#64748b', font: { size: 9 }, maxRotation: 45, minRotation: 30 } },
            },
          },
        });
      }

      // CTL / ATL — evolución de fitness y fatiga
      const ctlCtx = document.getElementById('chartCtl');
      if (ctlCtx) {
        chartCtl = new Chart(ctlCtx, {
          type: 'line',
          data: {
            labels,
            datasets: [
              {
                label: 'CTL (fitness)',
                data: ctls,
                borderColor:     'rgba(59, 130, 246, 1)',
                backgroundColor: 'rgba(59, 130, 246, 0.08)',
                borderWidth: 2,
                pointRadius: 3,
                tension: 0.4,
                fill: false,
              },
              {
                label: 'ATL (fatiga)',
                data: atls,
                borderColor:     'rgba(109, 40, 217, 1)',
                backgroundColor: 'rgba(109, 40, 217, 0.06)',
                borderWidth: 2,
                pointRadius: 3,
                tension: 0.4,
                fill: false,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { display: true, position: 'bottom', labels: { boxWidth: 12, font: { size: 11 }, color: '#475569' } },
              tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(1)} km` } },
            },
            scales: {
              y: { beginAtZero: true, grid: { color: '#f1f5f9' }, ticks: { color: '#64748b', font: { size: 11 } } },
              x: { grid: { display: false }, ticks: { color: '#64748b', font: { size: 9 }, maxRotation: 45, minRotation: 30 } },
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
              borderColor:     'rgba(109, 40, 217, 1)',
              backgroundColor: 'rgba(109, 40, 217, 0.08)',
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
                grid: { color: '#f1f5f9' },
                ticks: { stepSize: 0.5, color: '#64748b', font: { size: 11 } },
              },
              x: { grid: { display: false }, ticks: { color: '#64748b', font: { size: 9 }, maxRotation: 45, minRotation: 30 } },
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

    // ── Navegación por pestañas ───────────────────────────────────────────
    switchTab(tab) {
      this.activeTab = tab;
      // Chart.js no puede calcular dimensiones en display:none — forzar resize al revelar
      if (tab === 'analitica') {
        this.$nextTick(() => {
          if (chartKm)   chartKm.resize();
          if (chartCtl)  chartCtl.resize();
          if (chartAcwr) chartAcwr.resize();
        });
      }
    },
  };
}

function acwrColor(v) {
  if (v === null || v === undefined || isNaN(v)) return 'gray';
  if (v < 0.8 || v > 1.5) return 'red';
  if (v > 1.3) return 'yellow';
  return 'green';
}
