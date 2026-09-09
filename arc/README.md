# Recruitment application workflow

This directory contains the recruitment British-English gettext catalogue. The
deployment image extends the unmodified upstream `pretalx/standalone` image
and adds the catalogue and ARC application-workflow plugin. No upstream
pretalx source files are edited. The plugin installs runtime adapters and extends
applicant templates.

Enable **Recruitment application workflow** under a call's organiser settings. On
activation, it hides the conference-specific built-in fields and adds private
application fields for a CV, cover letter, evidence of the required degree,
optional project repository and privacy acknowledgement. Interview adjustments are
requested by email only from invited applicants. It also generates application titles in the form `Application ABC123 — Position`.

Recompile after editing the PO file:

```shell
msgfmt --check --check-format \
  --output-file=arc/locale/en/LC_MESSAGES/django.mo \
  arc/locale/en/LC_MESSAGES/django.po
```

## Interview privacy

The call itself must be public for applicants to reach the application form,
but the interview schedule must not be. For every recruitment call, keep
these event feature flags set as follows:

```text
show_schedule = false
show_featured = never
show_widget_if_not_public = false
submission_public_review = false
use_feedback = false
```

`show_schedule = false` protects the schedule pages, versioned schedule URLs,
exports, feeds, API results, interview rooms, and applicant profile pages.
`show_featured = never` is also required: featured applications have a
separate public visibility path and can otherwise be exposed while the
schedule itself is hidden. Public review links are disabled separately so an
applicant cannot create a shareable application URL. The plugin enforces these
privacy flags for every call in this dedicated ARC image, even before the
workflow is activated for that call. Organisers can still use the work-in-
progress schedule internally to arrange interviews; releasing a version does
not make it publicly accessible.

## Private documents

The plugin installs private storage for file answers, temporary application-form
uploads and cached files used by API uploads and export jobs. They live under
`/data/arc-private`, not `/public/media`. Existing answer-file links automatically
use `/arc-files/`, where each download checks the current user's ownership or
application access and question visibility. Files are streamed as non-cacheable
attachments; public-question settings never grant access to these downloads.

This protection applies even when the ARC form is disabled for a call. Document ZIPs are generated on demand with per-document access checks and
streamed from a temporary file that is discarded when the response closes.
No persistent export archives or cached export links are used. Static schedule
website exports are disabled. Private cached storage remains for API upload staging.

At startup the plugin attaches Django field storage, form temporary storage and
private-file cleanup hooks to the upstream classes. Replacements and deletions
remove private files after transaction commit. This adapter is tested against
pretalx 2026.2.1; repeat the plugin tests when upgrading the base image.

Rebuild the ARC image to install this change. This is for a fresh deployment:
no existing public files are migrated. Follow the
[production guide](../deployment/PRODUCTION.md) to block old media upload paths,
keep the private directory outside proxy document roots and verify permissions.

## Email templates

The installed defaults are maintained in the plugin's [email module](plugin/src/pretalx_arc_application/emails.py).
Review and customise each call's templates in the organiser interface before
sending. Email sign-offs use the individual recruitment call’s name.


## Workflow adapters

The plugin supports an empty General step and retained uploads on back-navigation.
It installs per-call receipt, shortlist, pre-interview rejection and interview
notification defaults. Receipts omit application content. Invitations and calendar
attachments contain full private interview details, including rescheduling before
an applicant confirms. Applicants can see released interview details on their own
application and confirmation screens.

Use `pretalx.submission.domain.submission.set_submission_state` and
`set_pending_state` / `apply_pending_state` in scripts. Direct saved or bulk state
changes raise an error. Opposite decision drafts and superseded invitation drafts
are removed; delivery checks current decisions and calendar details again. Already
sent correspondence stays in the audit history. Bulk event privacy-flag updates
are normalised, and privacy flags are enforced when read.

These runtime adapters target pretalx 2026.2.1. Run the regression suite and inspect
these seams before upgrading. Default templates are used for new calls and missing
role templates; organisers' saved customisations are not overwritten. Automatic
update metadata checks default to disabled; arrange manual update monitoring.


## Manual email handover

The development overlay enables a protected **Manual delivery** screen in orga.
With an empty SMTP host (`PRETALX_MAIL_HOST=""`), email is prepared locally for an instance
administrator to pass on, including reviewer invitations and account emails.
No SMTP account or separate mailbox access is required. See the
[manual delivery guide](../deployment/MANUAL_EMAIL.md) for collection, attachments,
permissions and deployment settings.

## Recruitment privacy launch

Configure Durham-approved policy values under **Settings → Recruitment privacy**.
Intake remains closed until approval is recorded. See the
[privacy launch and operations guide](../deployment/RECRUITMENT_PRIVACY.md) for
withdrawal erasure, retention, reviewer defaults, readiness checks and CI evidence.
