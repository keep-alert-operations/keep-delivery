#!/bin/sh
set -eu
# The SCC assigns the actual UID and a writable PVC group. Never chown the PVC.
# Copy the private key into a UID-owned 0600 file; projected secrets are root-owned.
umask 077
mkdir -p /tmp/postgres-tls
cp /run/postgres-tls/tls.crt /tmp/postgres-tls/tls.crt
cp /run/postgres-tls/tls.key /tmp/postgres-tls/tls.key
chmod 600 /tmp/postgres-tls/tls.key
exec /usr/local/bin/docker-entrypoint.sh "$@" \
  -c unix_socket_directories=/var/run/postgresql \
  -c ssl=on \
  -c ssl_cert_file=/tmp/postgres-tls/tls.crt \
  -c ssl_key_file=/tmp/postgres-tls/tls.key \
  -c hba_file=/etc/postgres/pg_hba.conf
