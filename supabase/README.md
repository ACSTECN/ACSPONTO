# Migracao Inicial Para Supabase

## Arquivos

- `supabase/schema.sql`: cria tabelas, constraints, indices e triggers.

## Ordem sugerida de migracao dos dados

As tabelas `usuarios` e `equipes` se referenciam entre si:

- `usuarios.equipe_id -> equipes.id`
- `equipes.supervisor_id -> usuarios.id`

Por isso, a importacao dos dados deve seguir esta ordem:

1. Criar a estrutura rodando `supabase/schema.sql` no SQL Editor do Supabase.
2. Importar `usuarios` com a coluna `equipe_id` vazia ou nula.
3. Importar `equipes` com a coluna `supervisor_id` vazia ou nula.
4. Atualizar `usuarios.equipe_id`.
5. Atualizar `equipes.supervisor_id`.
6. Importar `jornadas`.
7. Importar `escalas`.
8. Importar `ponto`.

## Mapeamento das tabelas

- `Usuarios` -> `usuarios`
- `Equipes` -> `equipes`
- `Escalas` -> `escalas`
- `Jornadas` -> `jornadas`
- `Ponto` -> `ponto`

## Conversao de colunas

- `SenhaTemporaria` -> `senha_temporaria`
- `LoggedIn` -> `logged_in`
- `EquipeID` -> `equipe_id`
- `NomeEquipe` -> `nome_equipe`
- `SupervisorID` -> `supervisor_id`
- `UsuarioID` -> `usuario_id`
- `DiaSemana` -> `dia_semana`
- `VoltaPausa` -> `volta_pausa`
- `JornadaEsperada` -> `jornada_esperada`
- `DataRegistro` -> `data_registro`
- `HoraRegistro` -> `hora_registro`
- `MotivoAbono` -> `motivo_abono`
- `HoraCorrigida` -> `hora_corrigida`
- `MotivoCorrecao` -> `motivo_correcao`
- `CorrigidoPor` -> `corrigido_por`
- `DataHoraAlteracao` -> `data_hora_alteracao`

## Observacoes importantes

- O schema foi criado pensando no backend atual em Flask, sem usar Supabase Auth por enquanto.
- O login continua sendo pela tabela `usuarios` com `bcrypt`, igual ao seu sistema atual.
- O `app.py` ja foi adaptado para PostgreSQL/Supabase usando `psycopg`.
- No Supabase, o ideal e usar variaveis de ambiente no backend e nunca expor a service role key no frontend.

## Variaveis de ambiente

Use uma destas opcoes:

- `DATABASE_URL`
- `SUPABASE_DB_URL`

Exemplo:

```env
DATABASE_URL=postgresql://postgres:sua-senha@db.seu-projeto.supabase.co:5432/postgres?sslmode=require
FLASK_SECRET_KEY=troque-esta-chave
FLASK_DEBUG=0
```
