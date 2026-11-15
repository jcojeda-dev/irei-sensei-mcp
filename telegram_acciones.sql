-- =========================================================
-- Acciones pendientes de confirmación del bot de Telegram.
-- El bot nunca ejecuta cambios por sí solo: crea una fila aquí, muestra
-- botones Confirmar/Cancelar y solo ejecuta si la misma persona confirma
-- dentro de 10 minutos. Cada fila se usa una sola vez.
-- Ejecutar en Supabase → SQL Editor.
--
-- SI YA EJECUTASTE ESTE ARCHIVO ANTES, ejecuta solo el BLOQUE EXTRA del final
-- (agrega la acción 'generar_pdf').
-- =========================================================
create table if not exists public.acciones_pendientes_telegram (
  id uuid primary key default gen_random_uuid(),
  chat_id bigint not null,
  accion text not null check (accion in ('enviar_recordatorio','generar_plan','generar_pdf','actualizar_membresia')),
  params jsonb not null,
  descripcion text not null,       -- texto armado por el servidor, no por la IA
  usada boolean not null default false,
  creado_en timestamptz not null default now()
);

create index if not exists idx_acciones_pendientes_creado
  on public.acciones_pendientes_telegram (creado_en desc);

-- RLS activado y SIN políticas: nadie con la clave pública puede leerla ni
-- escribirla. Solo el servidor MCP (service role) accede.
alter table public.acciones_pendientes_telegram enable row level security;

-- =========================================================
-- BLOQUE EXTRA (solo si la tabla ya existía): permitir la acción 'generar_pdf'
-- =========================================================
alter table public.acciones_pendientes_telegram
  drop constraint if exists acciones_pendientes_telegram_accion_check;
alter table public.acciones_pendientes_telegram
  add constraint acciones_pendientes_telegram_accion_check
  check (accion in ('enviar_recordatorio','generar_plan','generar_pdf','actualizar_membresia'));
