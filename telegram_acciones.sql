-- =========================================================
-- Acciones pendientes de confirmación del bot de Telegram.
-- El bot nunca ejecuta cambios por sí solo: crea una fila aquí, muestra
-- botones Confirmar/Cancelar y solo ejecuta si la misma persona confirma
-- dentro de 10 minutos. Cada fila se usa una sola vez.
-- Ejecutar en Supabase → SQL Editor.
-- =========================================================
create table if not exists public.acciones_pendientes_telegram (
  id uuid primary key default gen_random_uuid(),
  chat_id bigint not null,
  accion text not null check (accion in ('enviar_recordatorio','generar_plan','actualizar_membresia')),
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
