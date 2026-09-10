# Built from the Debian package rather than pulled from a third-party image so
# the pool configuration is visible in this repository and pinned by us. The
# two config files beside this Dockerfile are the point of the service.
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install --no-install-recommends -y pgbouncer postgresql-client \
 && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["pgbouncer"]
CMD ["/etc/pgbouncer/pgbouncer.transaction.ini"]
