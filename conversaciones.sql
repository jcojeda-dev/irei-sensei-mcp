-- =========================================================
-- Conversaciones del bot de WhatsApp (para revisarlas en /admin)
-- Ejecutar en Supabase → SQL Editor.
-- =========================================================
create table if not exists public.conversaciones (
  id uuid primary key default gen_random_uuid(),
  usuario_id uuid references public.usuarios(id) on delete set null,
  whatsapp text not null,
  mensaje_alumno text not null,
  respuesta_ia text not null,
  derivar boolean not null default false,   -- true = la IA pidió que Isamu intervenga
  atendida boolean not null default false,  -- Isamu/JC la marcan al resolverla
  creado_en timestamptz not null default now()
);

create index if not exists idx_conversaciones_fecha
  on public.conversaciones (creado_en desc);
create index if not exists idx_conversaciones_pendientes
  on public.conversaciones (derivar, atendida);

-- RLS: solo admins leen/modifican. El servidor MCP escribe con service role.
alter table public.conversaciones enable row level security;

create policy "admin gestiona conversaciones" on public.conversaciones
  for all using (public.es_admin());
