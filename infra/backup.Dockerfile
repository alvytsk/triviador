# syntax=docker/dockerfile:1

# §10.8's backup job. Alpine's own `postgresql17-client` matches
# `db`'s `postgres:17-alpine` exactly (compose.yaml), and its `rclone`
# package is new enough for `--one-way` and env-var-driven remote config.
# `flock`, `date` (with `-D` for parsing and `%G`/`%V` for ISO weeks) and
# `awk` all come from busybox, already in the base image.
FROM alpine:3.21
RUN apk add --no-cache postgresql17-client rclone
COPY infra/backup.sh /usr/local/bin/backup.sh
RUN chmod +x /usr/local/bin/backup.sh
ENTRYPOINT ["/usr/local/bin/backup.sh"]
