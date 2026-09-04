ARG PRETALX_IMAGE=pretalx/standalone:latest
FROM ${PRETALX_IMAGE}

USER root

# Install the ARC workflow as an independent pretalx plugin. No upstream
# Python modules or templates are patched.
COPY arc/plugin /opt/pretalx-arc-application
RUN python3 -m pip install --no-cache-dir --no-deps --no-build-isolation \
    /opt/pretalx-arc-application

# ARC is an additive reskin of the upstream image. Keeping the catalogue here
# avoids carrying a fork of pretalx and makes upgrades a base-image change.
COPY --chown=pretalxuser:pretalxuser \
    arc/locale/en/LC_MESSAGES/django.po \
    arc/locale/en/LC_MESSAGES/django.mo \
    /pretalx/src/pretalx/locale/en/LC_MESSAGES/

USER pretalxuser
