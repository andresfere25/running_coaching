-- ============================================================
-- Migración 008 — Agregar columna phone a tabla athletes
--
-- El Portal (arathleteslab.com) guarda nombre + teléfono del atleta
-- cuando el coach crea el invite desde el Panel Admin.
-- El teléfono se usa SOLO para enviar el link de invitación (WhatsApp).
-- No se muestra en el dashboard del atleta.
--
-- Ejecutar en: Supabase Dashboard → SQL Editor
-- ============================================================

ALTER TABLE athletes
    ADD COLUMN IF NOT EXISTS phone TEXT;

COMMENT ON COLUMN athletes.phone IS 'Teléfono del atleta (WhatsApp). Uso interno del coach — no visible en el dashboard.';
