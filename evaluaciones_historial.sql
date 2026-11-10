-- =========================================================
-- Historial de mediciones: una evaluación por alumno y fecha
-- y whatsapp guardado solo con dígitos (ej. 51970463993).
-- Ejecutar en Supabase → SQL Editor, UN BLOQUE A LA VEZ.
-- =========================================================

-- BLOQUE 1: ¿hay números repetidos que haya que revisar antes de normalizar?
select regexp_replace(whatsapp, '\D', '', 'g') as digitos,
       count(*) as filas,
       string_agg(nombre || ' (' || whatsapp || ')', ' | ') as detalle
from public.usuarios
group by 1
having count(*) > 1;
-- Si devuelve filas, borra a mano las repetidas en Table Editor antes del BLOQUE 2.

-- BLOQUE 2: normalizar los whatsapp existentes a solo dígitos
update public.usuarios
set whatsapp = regexp_replace(whatsapp, '\D', '', 'g')
where whatsapp ~ '\D';

-- BLOQUE 3: quitar evaluaciones duplicadas (mismo alumno y fecha), dejando la más reciente
delete from public.evaluaciones e
using public.evaluaciones d
where e.usuario_id = d.usuario_id
  and e.fecha = d.fecha
  and e.creado_en < d.creado_en;

-- BLOQUE 4: impedir duplicados a futuro (lo necesita el upsert del sync)
create unique index if not exists uq_evaluaciones_usuario_fecha
  on public.evaluaciones (usuario_id, fecha);
