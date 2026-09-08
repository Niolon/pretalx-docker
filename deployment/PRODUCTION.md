# Production deployment and application privacy

This guide is for the dedicated ARC recruitment image on a host you administer,
with a reverse proxy providing HTTPS. The checked-in configuration is a development
starting point. Do not collect real applications with it unchanged.

## Private document downloads

The ARC plugin stores file answers under `DATA_DIR/arc-private` (normally
`/data/arc-private`), outside public media storage. Its `/arc-files/` endpoint
checks the logged-in user's access to the specific application and question
before streaming an attachment. Copied URLs do not grant access. Responses are
marked private and non-cacheable, and use a non-executable content type.

Existing UI file links use this endpoint automatically. Applicants can download
their own documents; staff need both access to the relevant application and
visibility of the question. Reviewer assignments, tracks, review-phase visibility
and question team restrictions apply. Anonymous users and unrelated applicants
receive 404. Public-question settings do not grant document access.

Temporary application-form uploads and cached API uploads/export archives also
use private storage. Temporary files have no direct download access; generated
exports continue through their originating organiser views. This protection is
installed instance-wide, regardless of whether a call enables the ARC form.

This deployment is new and has no existing uploads, so no data migration is
needed. This plugin does not migrate old public files. If reusing a previously
populated instance, stop and account for old documents, temporary uploads and
cached exports before launch; changing storage does not remove public copies.

The proxy must pass `/arc-files/` to pretalx and must never serve `/data` or
`/data/arc-private` as a static directory. Do not cache these responses at the
proxy or CDN. Keep a defensive block on the former public upload paths; nginx:

```nginx
# Keep these ahead of other regex locations. Do not use a ^~ /media/ location
# that would prevent these regexes from being evaluated.
location ~ ^/media/[^/]+/question_uploads/ {
    return 404;
}
location ~ ^/media/(cfp_uploads|cachedfiles)/ {
    return 404;
}
```

Adapt these rules if the media URL changes. Other media such as avatars and event
logos remain public; do not use those fields for confidential documents. Do not
enable directory listings. Disable shared proxy/CDN caching for authenticated
pages and API responses as well. Previously downloaded copies cannot be recalled.

## Production configuration

1. Use only `docker-compose.yml` plus an explicitly reviewed production overlay.
   Do not include `compose.dev.yml` on the production host. It exposes an
   unauthenticated Mailpit inbox and SMTP listener on all interfaces, despite the
   development instructions referring to localhost. The running development
   stack must not contain real applicant data or real account-reset messages.
2. Set `[site] debug = False` and `url = https://applications.example.org` in the
   production configuration. Environment variables override config files: check
   that `PRETALX_DEBUG` and `PRETALX_SITE_URL` agree. The development overlay's
   `PRETALX_DEBUG=False` must not be the only thing keeping debug disabled.
3. Replace the placeholder database password in both the database configuration
   and the database service's initialisation settings. Store production secrets
   outside the Git checkout, with restricted access. Mount the production config
   read-only over `/etc/pretalx/pretalx.cfg`; ensure container UID 999 can read it.
   Do not commit a populated config or paste expanded Compose configuration into
   tickets: it can contain credentials. Changing `POSTGRES_PASSWORD` on an
   existing database volume does not rotate the database role's password; rotate
   that role as well and update the application configuration together.
4. Replace the application's published ports with `127.0.0.1:8346:80` when nginx
   runs on the host. With a containerised proxy, use a private Docker network and
   remove the application's published host ports instead. Keep PostgreSQL and
   Redis unpublished and do not attach unrelated containers to their network.
5. Configure SMTP through the institution's approved mail service, using TLS or
   SSL as required by that service. Do not send credentials or recruitment mail
   over an untrusted plaintext SMTP connection. Review event-specific SMTP
   settings too: they can override the instance defaults.
6. Pin the tested upstream image version/digest in `build.args.PRETALX_IMAGE`,
   and record the resulting ARC image digest. Pin the database and Redis images
   too. Schedule security updates and repeat the privacy checks after upgrades;
   the current `latest` tags do not identify the code you reviewed. Always deploy
   the ARC image: replacing it with upstream alone removes its privacy guards.

For a host-based proxy, these are the relevant parts of a production overlay
(replace the domain and mount source before use):

```yaml
services:
  pretalx:
    environment:
      PRETALX_DEBUG: "False"
      PRETALX_SITE_URL: https://applications.example.org
    ports: !override
      - "127.0.0.1:8346:80"
    volumes:
      - /etc/arc/pretalx.cfg:/etc/pretalx/pretalx.cfg:ro
```

This is a partial overlay, not a complete deployment: configure the password and
image pins above too. `!override` requires Docker Compose 2.24.4 or newer. Check
the merged configuration locally before starting it, taking care not to share
secret values. An ordinary additional port entry can leave the base port 80
published as well.

## Reverse proxy and hosting

Expose only HTTPS and an HTTP-to-HTTPS redirect to applicants. Prevent direct
access to Gunicorn, including via IPv6. Set the expected Host and forwarded
protocol/client headers at the trusted proxy, and configure the application to
trust only the actual proxy path. Test the direct origin as well as the public
hostname if the hosting platform provides a separate origin URL.

The nginx file in `reverse-proxy-examples/` is an illustrative example. Adapt its
hostname, certificate and volume paths, validate with `nginx -t`, and retain the
private-upload restrictions above. Serve permitted
uploads as attachments with `X-Content-Type-Options: nosniff` and a non-executable
content type. Keep the repository, config, database, logs, backups and `/data`
outside every public document root. Serve only the intended static/media paths.

Check the hosting control panel for automatic public file sharing, backup links,
staging copies, analytics and CDN caching. Avoid caching authenticated HTML or
API responses. Do not put applicant identifiers or document URLs into analytics.
Restrict and retain access logs carefully: URLs can include invitation/reset
tokens. Host administrators, backup operators and the chosen mail provider remain
trusted parties; self-hosting does not conceal plaintext from those operators.

## Prevent organiser mistakes

The ARC plugin locks schedule, featured-application, public-review, feedback and
attendee-signup flags on normal event saves, including events where the workflow
is not enabled. Publishing an interview schedule should therefore not publish
applicant pages. These are application guards, not a substitute for testing the
actual deployed routes. Direct database updates can bypass save signals.

Question privacy is currently only initialised when the workflow is activated.
An organiser can subsequently change “Show answers to reviewers”, “Publish
answers” or team access. Keep ARC questions private; retain reviewer hiding for
the declaration. Interview adjustments are requested only from invited applicants
by reply to the interview email, not through the application form. Restrict that
recruitment mailbox to staff who need access. Consider enforcing question privacy in code
before delegating event-settings permissions.

Give reviewers only the reviewer role for the specific recruitment call. Team
permissions are additive: joining a broader organiser team defeats reviewer-only
restrictions. Restrict team invitations, settings, exports and API tokens to
staff who need them. Check recipients and templates before sending bulk emails;
exports and downloaded documents remain sensitive after they leave the server.
Application titles include applicant names, so calendars and email subjects can
identify applicants even without attached documents. Do not promise anonymous
review while reviewers can read identifying CVs.

## Backups and retention

Back up `pretalx-database`, `pretalx-data` (including `.secret`) and the uploaded
media in `pretalx-public`. With this Compose configuration, `/data/arc-private`
contains private documents and `/public/media` contains original public uploads
such as avatars. Neither is regenerable. `/public/static` is regenerable.
Encrypt backups, restrict restore/export access and test restoration
in an isolated environment with outgoing mail disabled. Apply the recruitment
retention period to documents, database records, exports, mail, logs and backups.

## Outgoing update metadata

The plugin defaults automatic update checks to disabled. If an administrator
enables the upstream check, it sends instance, version and plugin metadata and
event counts to pretalx.com. Keep it disabled if this deployment must not send
that metadata, and arrange manual update monitoring separately.

## Checks before opening the call

Review the [workflow fixes and validation](../arc/WORKFLOW_REVIEW.md) and repeat
the checks below against the actual production proxy before opening applications.

Use synthetic applications and separate applicant, reviewer and organiser
accounts. Check through the actual HTTPS proxy:

- Run `docker compose exec pretalx python3 -m pretalx check --deploy` with the
  production Compose files selected. Investigate warnings; passing this command
  does not test media permissions or the hosting firewall.
- Verify only intended public ports are reachable from another machine, over
  IPv4 and IPv6. Mailpit, PostgreSQL, Redis and the application origin must not
  be publicly reachable.
- Open a copied CV URL while logged out and as another applicant: both must
  fail. Verify the owner and authorised staff can download it, and that a
  restricted reviewer cannot. Repeat after removing staff access. Confirm the
  corresponding old `/media/` paths fail too, including temporary uploads and
  cached archives. Do this through the proxy, not only Django's test client.
- Publish a test interview schedule and mark an application featured. Verify
  logged-out schedule pages, old versions, exports, feeds, widgets, profiles,
  public-review links and API responses reveal no applicant/interview data.
- Confirm the form does not request interview adjustments. Check reviewer-hidden
  questions, including exports and API access where available. Review every team
  membership and question access setting.
- Verify HTTPS redirects and secure cookies, and ensure private responses are
  not served from shared caches. Use synthetic data to test error handling with
  debug disabled. Test email delivery and a backup restore.

Upstream references: [configuration](https://docs.pretalx.org/administrator/configure/),
[installation and proxy requirements](https://docs.pretalx.org/administrator/installation/),
[team permissions](https://docs.pretalx.org/user/organisers/), and
[security support](https://docs.pretalx.org/legal/security/).


## Email without SMTP access

Use [manual email delivery](MANUAL_EMAIL.md) to prepare protected emails for an
instance administrator to pass on through a separate mail environment. Set
`PRETALX_MAIL_HOST=""` on both web and worker processes to leave server SMTP unconfigured. Email using those server settings,
including account access and reviewer invitations, is captured inside the
application rather than sent automatically. The mailbox needs the same protection
and retention controls as the rest of the recruitment data.
