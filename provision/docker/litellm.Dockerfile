# Pin to a known stable LiteLLM release.
# Using `main-latest` can pull breaking runtime changes (e.g. Prisma/query-engine regressions).
FROM ghcr.io/berriai/litellm:v1.81.12-stable.2

# Upgrade glibc to 2.43 so libopus (dependency of libsndfile) can load.
# The base image ships glibc 2.42 but the Wolfi libopus package is compiled
# against 2.43, causing "GLIBC_2.43 not found" at runtime.
RUN apk add --no-cache \
    glibc=2.43-r0 ld-linux=2.43-r0 \
    glibc-locale-posix=2.43-r0 libcrypt1=2.43-r0 \
    libsndfile
