# adi1 container recreate commands (env values redacted)

Generated from the source container configs. Secret-looking env values are
redacted here; on adi1 they are applied from `~/podman-migration/rest.env` and
the extracted entrypoint payloads (see docs/adi1-podman-migration.md).

## homeassistant

```sh
podman run -d --name homeassistant \
  -p 8123:8123 \
  --restart unless-stopped \
  -e "S6_BEHAVIOUR_IF_STAGE2_FAILS=2" \
  -e "S6_SERVICES_READYTIME=50" \
  -e "S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0" \
  -e "S6_CMD_WAIT_FOR_SERVICES=1" \
  -e "TZ=America/Los_Angeles" \
  -e "S6_SERVICES_GRACETIME=240000" \
  -e "UV_SYSTEM_PYTHON=true" \
  -e "UV_EXTRA_INDEX_URL=https://wheels.home-assistant.io/musllinux-index/" \
  -e "UV_NO_CACHE=true" \
  -v ha-config:/config:Z \
  docker.io/homeassistant/home-assistant:stable
```

## rai-spoolman

```sh
podman run -d --name rai-spoolman \
  -p 8000:8000 \
  --restart unless-stopped \
  -e "SPOOLMAN_DB_TYPE=sqlite" \
  -v spoolman-data:/home/app/.local/share/spoolman:Z \
  ghcr.io/donkie/spoolman:latest
```

## cyberdesk-pg

```sh
podman run -d --name cyberdesk-pg \
  -p 5432:5432 \
  --restart no \
  -e "POSTGRES_PASSWORD=<redacted>" \
  -e "POSTGRES_USER=cyberdesk" \
  -e "POSTGRES_DB=cyberdesk" \
  -v <source-anonymous-volume>:/var/lib/postgresql/data:Z \
  docker.io/library/postgres:17-alpine
```

## handyman-pg

Port deviation on adi1: host side binds **5433** (source used 5432, which
collides with cyberdesk-pg when both run).

```sh
podman run -d --name handyman-pg \
  -p 5433:5432 \
  --restart no \
  -e "POSTGRES_USER=handyman" \
  -e "POSTGRES_PASSWORD=<redacted>" \
  -e "POSTGRES_DB=handyman_dev" \
  -v <source-anonymous-volume>:/var/lib/postgresql/data:Z \
  docker.io/library/postgres:16
```

## supabase_db_platform

```sh
# entrypoint payload: extract-container-entrypoint.sh supabase_db_platform
podman run -d --name supabase_db_platform \
  -p 54322:5432 \
  --network supabase_network_platform \
  --network-alias db \
  --network-alias db.supabase.internal \
  --restart unless-stopped \
  -e "JWT_SECRET=<redacted>" \
  -e "POSTGRES_PASSWORD=<redacted>" \
  -e "JWT_EXP=3600" \
  -e "POSTGRES_HOST=/var/run/postgresql" \
  -e "POSTGRES_INITDB_ARGS=--allow-group-access --locale-provider=icu --encoding=UTF-8 --icu-locale=en_US.UTF-8" \
  -e "POSTGRES_DB=postgres" \
  -e "POSTGRES_USER=supabase_admin" \
  -v supabase_db_platform:/var/lib/postgresql/data:Z \
  -v /home/adi/podman-migration:/migration-src:ro \
  --entrypoint /bin/sh public.ecr.aws/supabase/postgres:17.6.1.106 \
  /migration-src/adi1-supabase-db-entrypoint.sh
```

## supabase_rest_platform

```sh
# env file: scp ~/podman-migration/rest.env (contains PGRST_JWT_SECRET)
podman run -d --name supabase_rest_platform \
  --network supabase_network_platform \
  --network-alias rest \
  --restart unless-stopped \
  --env-file /home/adi/podman-migration/rest.env \
  -e "PGRST_ADMIN_SERVER_PORT=3001" \
  -e "PGRST_DB_EXTRA_SEARCH_PATH=public,extensions" \
  -e "PGRST_DB_ANON_ROLE=anon" \
  -e "PGRST_DB_URI=postgresql://authenticator:<redacted>@supabase_db_platform:5432/postgres" \
  -e "PGRST_DB_SCHEMAS=public,graphql_public" \
  -e "PGRST_DB_MAX_ROWS=1000" \
  public.ecr.aws/supabase/postgrest:v14.10
```

## supabase_pg_meta_platform

```sh
podman run -d --name supabase_pg_meta_platform \
  --network supabase_network_platform \
  --network-alias pg_meta \
  --restart unless-stopped \
  -e "PG_META_DB_NAME=postgres" \
  -e "PG_META_DB_USER=postgres" \
  -e "PG_META_DB_PASSWORD=<redacted>" \
  -e "PG_META_PORT=8080" \
  -e "PG_META_DB_HOST=supabase_db_platform" \
  -e "PG_META_DB_PORT=5432" \
  public.ecr.aws/supabase/postgres-meta:v0.96.4
```

## supabase_kong_platform

```sh
# entrypoint payload: extract-container-entrypoint.sh supabase_kong_platform
podman run -d --name supabase_kong_platform \
  -p 54321:8000 \
  --network supabase_network_platform \
  --network-alias kong \
  --network-alias api.supabase.internal \
  --restart unless-stopped \
  -e "KONG_NGINX_PROXY_PROXY_BUFFER_SIZE=160k" \
  -e "KONG_NGINX_PROXY_PROXY_BUFFERS=64 160k" \
  -e "KONG_SSL_CERT_KEY=/home/kong/localhost.key" \
  -e "KONG_DATABASE=off" \
  -e "KONG_DECLARATIVE_CONFIG=/home/kong/kong.yml" \
  -e "KONG_DNS_ORDER=LAST,A,CNAME" \
  -e "KONG_PLUGINS=request-transformer,cors" \
  -e "ASSET=ce" \
  -e "KONG_NGINX_WORKER_PROCESSES=1" \
  -e "KONG_PORT_MAPS=54321:8000" \
  -e "KONG_SSL_CERT=/home/kong/localhost.crt" \
  -v /home/adi/podman-migration:/migration-src:ro \
  --entrypoint /bin/sh \
  public.ecr.aws/supabase/kong:2.8.1
```
