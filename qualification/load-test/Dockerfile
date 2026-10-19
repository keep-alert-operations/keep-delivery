# Supply an approved grafana/k6-compatible image mirrored into the private registry.
# Pin K6_IMAGE by digest; this image never downloads scripts or npm packages at runtime.
ARG K6_IMAGE
FROM ${K6_IMAGE}
WORKDIR /work
COPY --chmod=0555 *.js /work/
COPY --chmod=0555 lib /work/lib
COPY --chmod=0444 profiles /work/profiles
USER 10001:0
ENTRYPOINT ["k6"]
CMD ["run", "/work/ingestion.js"]
