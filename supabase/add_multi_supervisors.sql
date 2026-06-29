create table if not exists public.equipe_supervisores (
    equipe_id bigint not null,
    usuario_id bigint not null,
    created_at timestamptz not null default now(),
    primary key (equipe_id, usuario_id)
);

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'equipe_supervisores_equipe_id_fkey'
    ) then
        alter table public.equipe_supervisores
            add constraint equipe_supervisores_equipe_id_fkey
            foreign key (equipe_id)
            references public.equipes (id)
            on delete cascade
            deferrable initially deferred;
    end if;
end
$$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'equipe_supervisores_usuario_id_fkey'
    ) then
        alter table public.equipe_supervisores
            add constraint equipe_supervisores_usuario_id_fkey
            foreign key (usuario_id)
            references public.usuarios (id)
            on delete cascade
            deferrable initially deferred;
    end if;
end
$$;

create index if not exists idx_equipe_supervisores_usuario_id
    on public.equipe_supervisores (usuario_id);

insert into public.equipe_supervisores (equipe_id, usuario_id)
select e.id, e.supervisor_id
from public.equipes e
where e.supervisor_id is not null
on conflict (equipe_id, usuario_id) do nothing;
