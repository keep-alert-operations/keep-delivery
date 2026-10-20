# Supply a reviewed, immutable official Redis image; runtime never downloads.
ARG REDIS_BASE
FROM ${REDIS_BASE}
USER root
RUN command -v redis-server && command -v redis-cli \
    && mkdir -p /data && chgrp 0 /data && chmod g=u /data
USER 1001:0
# Bypass the upstream entrypoint's root-oriented permission handling.
ENTRYPOINT ["redis-server"]
CMD ["/etc/redis/redis.conf"]
