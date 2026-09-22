# Built from the Debian package rather than pulled from a third-party image so
# the pool configuration is visible in this repository and pinned by us. The
# two config files beside this Dockerfile are the point of the service.
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install --no-install-recommends -y pgbouncer postgresql-client \
 && rm -rf /var/lib/apt/lists/*

# The Debian package creates no service user of its own, and pgbouncer refuses
# to start as root. Both ports it binds are unprivileged (6432, 6433), so an
# ordinary user is enough.
RUN useradd --system --no-create-home --shell /usr/sbin/nologin pgbouncer
USER pgbouncer

ENTRYPOINT ["pgbouncer"]
CMD ["/etc/pgbouncer/pgbouncer.transaction.ini"]
