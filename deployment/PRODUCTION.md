# Production deployment and application privacy

This guide is for the dedicated ARC recruitment image on a host you administer,
with a reverse proxy providing HTTPS. The checked-in configuration is a development
starting point. Do not collect real applications with it unchanged.

## Resolve private file access before collecting applications

**The current image and example proxy do not provide authenticated downloads for
uploaded application documents.** ARC makes questions private in the application,
but upstream stores file answers under `/media/<event>/question_uploads/` and
renders their storage URLs. The example nginx configuration serves all of
`/media/` directly, without application permissions. Anyone who obtains a CV,
transcript or reference URL can download it. Random filenames reduce discovery;
they do not restrict access or revoke a copied link when team membership changes.

Before launch, implement permission-checked downloads for these files, with
storage inaccessible through a public media alias. Authorisation must check the
specific application, event and question, including reviewer/team restrictions.
A protected internal nginx location can deliver a file after the application
authorises it. Merely requiring any logged-in account is insufficient: applicants
must not be able to download each other's documents. An alternative is to collect
documents through a separate approved system and remove these upload fields.

Until that work is complete, deny public access to question uploads at the proxy
and use synthetic data only. This deliberately makes current document links fail;
it is containment, not a working private-download implementation. For nginx:

```nginx
# Keep this ahead of other regex locations. Do not use a ^~ /media/ location
# that would prevent this regex from being evaluated.
location ~ ^/media/[^/]+/question_uploads/ {
    return 404;
}
```

Adapt this rule if the media URL changes. Check other media classes before using
them for sensitive material. Do not enable directory listings. Disable shared
proxy/CDN caching for private downloads and authenticated pages, and send
`Cache-Control: private, no-store` for private responses. Previously public files
also need cache purging and removal or URL rotation; changing a question setting
does not recall downloaded copies.

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

The nginx file in `reverse-proxy-examples/` is an old illustrative example, not a
production configuration. Use current TLS syntax (`listen 443 ssl`), validate
with `nginx -t`, and apply the private-upload restriction above. Serve permitted
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
adjustments and the declaration. Limit adjustments to the staff who need them
using the question's team restrictions. Consider enforcing these choices in code
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
media in `pretalx-public`. With this Compose configuration, `/public/media`
contains original uploads, not regenerable output. `/public/static` is
regenerable. Encrypt backups, restrict restore/export access and test restoration
in an isolated environment with outgoing mail disabled. Apply the recruitment
retention period to documents, database records, exports, mail, logs and backups.

## Checks before opening the call

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
  restricted reviewer cannot. Repeat after removing staff access. The current
  proxy deny rule alone will also block authorised users; this check remains a
  launch blocker until private downloads or an alternative workflow exist.
- Publish a test interview schedule and mark an application featured. Verify
  logged-out schedule pages, old versions, exports, feeds, widgets, profiles,
  public-review links and API responses reveal no applicant/interview data.
- Check adjustments using a reviewer account, including exports and API access
  where available. Review every team membership and question access setting.
- Verify HTTPS redirects and secure cookies, and ensure private responses are
  not served from shared caches. Use synthetic data to test error handling with
  debug disabled. Test email delivery and a backup restore.

Upstream references: [configuration](https://docs.pretalx.org/administrator/configure/),
[installation and proxy requirements](https://docs.pretalx.org/administrator/installation/),
[team permissions](https://docs.pretalx.org/user/organisers/), and
[security support](https://docs.pretalx.org/legal/security/).
