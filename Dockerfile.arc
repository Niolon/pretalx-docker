ARG PRETALX_IMAGE=pretalx/standalone:latest
FROM ${PRETALX_IMAGE}

# ARC is an additive reskin of the upstream image. Keeping the catalogue here
# avoids carrying a fork of pretalx and makes upgrades a base-image change.
COPY --chown=pretalxuser:pretalxuser \
    arc/locale/en/LC_MESSAGES/django.po \
    arc/locale/en/LC_MESSAGES/django.mo \
    /pretalx/src/pretalx/locale/en/LC_MESSAGES/
