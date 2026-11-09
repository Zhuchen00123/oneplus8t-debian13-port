# Source as the dedicated dsh account. No credentials are printed.
export HOME=/srv/dsh/home
export DSH_HOME=/srv/dsh/home
export DSH_TELEMETRY_DISABLED=1
export PATH=/srv/dsh/runtime/bin:/srv/dsh/dev/node/node_modules/.bin:/srv/dsh/app/node_modules/.bin:/usr/local/bin:/usr/bin:/bin
export TMPDIR=/srv/dsh/cache/tmp
export npm_config_cache=/srv/dsh/cache/npm
set -a
. /srv/dsh/private/provider.env
set +a
