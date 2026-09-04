# ARC application vocabulary overlay

This directory contains the ARC British-English gettext catalogue. The
deployment image extends the unmodified upstream `pretalx/standalone` image
and adds the catalogue and ARC application-workflow plugin. No upstream
pretalx Python modules or templates are modified.

Enable **ARC application workflow** under a call's organiser settings. On
activation, it hides the conference-specific built-in fields and adds private
application fields for a CV, cover letter, transcript, project repository,
referees, recommendation letter, interview adjustments and declaration. It
also generates application titles in the form `Applicant — position`.

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
