# syntax=docker/dockerfile:1
# A shell AND the garage CLI. dxflrs/garage:v1.1.0 ships no /bin/sh, so an
# init *service* cannot run a script inside that image — see
# backend/testing/garage-init.sh, which works around the same limitation on
# the host. The binary is copied from the identical pinned tag as the server,
# which is the property §10.3 requires: garage-init depends on CLI syntax.
FROM alpine:3.21
COPY --from=dxflrs/garage:v1.1.0 /garage /usr/local/bin/garage
COPY infra/garage/init.sh /usr/local/bin/init.sh
RUN chmod +x /usr/local/bin/init.sh
ENTRYPOINT ["/usr/local/bin/init.sh"]
