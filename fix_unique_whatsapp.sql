-- Corrección: agregar UNIQUE constraint en whatsapp (necesario para
-- que upsert(on_conflict="whatsapp") funcione desde sync_sheet_to_db).
alter table public.usuarios
  add constraint usuarios_whatsapp_key unique (whatsapp);
