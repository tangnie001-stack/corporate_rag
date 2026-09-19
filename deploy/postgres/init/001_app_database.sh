#!/bin/bash
# 仅在 PG 数据目录首次初始化时执行（既有卷不会重跑）。
# 本脚本以超级用户（POSTGRES_USER）身份运行，因此是本轮唯一能创建 vector 扩展的地方。
# 建应用库与应用账号；Langfuse 的库/账号由 compose 的 POSTGRES_DB/POSTGRES_USER 提供。
set -euo pipefail

: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD is required}"
: "${POSTGRES_APP_USER:=corporate_rag}"
: "${POSTGRES_APP_DB:=corporate_rag}"

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v app_user="$POSTGRES_APP_USER" \
     -v app_password="$POSTGRES_APP_PASSWORD" \
     -v app_db="$POSTGRES_APP_DB" <<'SQL'
-- 建角色（幂等）。注意是普通 LOGIN 角色，不是超级用户 —— 应用不需要也更不该有超级用户权限。
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user')
\gexec

-- CREATE DATABASE 不能在事务块内执行，故用 \gexec 做幂等
SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_user')
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'app_db')
\gexec
SQL

# 扩展必须在应用库内创建。vector 不是 trusted 扩展（实测：非超级用户会得到
# "permission denied to create extension / Must be superuser"），所以只能在这里
# 以超级用户身份做；迁移（用应用账号连库）建不了它，只做前置断言。
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_APP_DB" \
     -c "CREATE EXTENSION IF NOT EXISTS vector;"
