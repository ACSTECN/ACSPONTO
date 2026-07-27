ALTER TABLE public.usuarios
DROP CONSTRAINT IF EXISTS usuarios_hierarquia_check;

ALTER TABLE public.usuarios
ADD CONSTRAINT usuarios_hierarquia_check
CHECK (
    regexp_replace(lower(coalesce(hierarquia, '')), '\s+', '', 'g') ~ '^(admin|normal|staff|rh)(,(admin|normal|staff|rh))*$'
);
