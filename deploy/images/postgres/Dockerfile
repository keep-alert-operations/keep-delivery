# Build with a reviewed, immutable Debian-flavoured official PostgreSQL image.
# Example argument format: --build-arg POSTGRES_BASE=registry/postgres@sha256:...
ARG POSTGRES_BASE
FROM ${POSTGRES_BASE}
USER root
# The official Debian image supplies NSS wrapper for initdb under an unknown UID.
# Reject incompatible bases instead of granting anyuid or installing at startup.
RUN test -x /usr/local/bin/docker-entrypoint.sh \
    && test -n "$(find /usr -name libnss_wrapper.so -print -quit)" \
    && mkdir -p /var/lib/appchi-postgres \
    && chgrp 0 /var/lib/appchi-postgres \
    && chmod g=u /var/lib/appchi-postgres
COPY --chmod=0555 entrypoint.sh /usr/local/bin/appchi-postgres
ENV PGDATA=/var/lib/appchi-postgres/data
USER 1001:0
ENTRYPOINT ["/usr/local/bin/appchi-postgres"]
CMD ["postgres"]
