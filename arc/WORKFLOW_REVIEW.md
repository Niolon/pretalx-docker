# Recruitment workflow review and fixes — 7 September 2026

The initial review used pretalx 2026.2.1, a separate network-isolated development
stack, two synthetic applicants and Mailpit. Private document storage was
committed as `535295d` before the walkthrough. The defects found in that review
are now addressed by the recruitment plugin as described below.

## Workflow exercised

The original browser walkthrough created an organiser, call, position, public
advert and deadline, then submitted two applications with CVs and cover letters.
It applied pending shortlist and pre-interview rejection decisions, reviewed and
sent both email drafts, created an interview room, scheduled and released a slot,
sent the invitation with a calendar attachment and confirmed as the applicant.
Five delivered messages were inspected. No real recipients were contacted.

The original default form required a temporary title-field workaround. The new
HTTP integration test completes the default form without that workaround,
including going back to the upload step and continuing without re-uploading.
Reviewer score entry and reviewer invitation were not part of the browser
walkthrough; organiser shortlisting and document access were covered.

## Implemented fixes

- **Application submission:** A saved empty General step is treated as bound
  form data. Required upload controls recognise retained files, while server-side
  validation still requires a live file. The adjustment question is absent.
- **Export authorisation:** Document ZIPs are generated on demand, checking the
  requesting user’s access to each answer. A private temporary file is discarded
  when its response closes. No persistent export cache is created. Cached-file
  and task-ID download parameters return 404. Static schedule website downloads
  are disabled. Temporary form and API upload staging remains private.
- **Conflicting decisions:** An opposite decision removes obsolete draft mail.
  Sending checks the current decision, including pending changes, and the worker
  checks again immediately before SMTP delivery. The worker and domain decision
  transitions share database locks. Already sent correspondence remains intact.
  Invalid single-mail sends show an organiser error instead of a server error.
- **Invitations and rescheduling:** Text and calendar attachments include full
  dates, start/end times, timezone and offset, room, joining instructions and an
  authenticated application link. Calendar identifiers remain stable across
  changes and carry a sequence number. New invitation drafts replace obsolete
  drafts; delivery rejects an attachment that no longer matches released slots.
  Changes are notified even before the applicant confirms interest.
- **Incomplete arrangements:** Notification-enabled release fails validation
  before releasing a schedule if an invited application has no complete slot or
  its room lacks joining instructions. No partial invitation is sent.
- **Applicant screens:** Own application and confirmation pages show released
  private interview details. Other applicants and anonymous visitors cannot see
  these details. Confirmation wording refers to interest in interviewing.
- **Templates:** Receipt, shortlist, pre-interview rejection and initial/changed
  interview defaults are installed for new calls, using per-call sign-offs.
  Existing custom copy is preserved. Receipts do not append application answers
  or document URLs. Adjustments are requested only in interview invitations.
- **Programmatic safeguards:** Direct saved/bulk decision changes raise an error;
  scripts must use the submission domain transition functions. Bulk event privacy
  updates are normalised and forced privacy flags are enforced when read.
- **Defaults and wording:** The default position is named “Position”, public-name
  instructions are removed, and automatic update metadata checks default off.

## Validation and limits

The plugin regression suite covers private document ownership, reviewer access,
revoked and cross-call export access, cached-ID rejection, default submission,
retained uploads, decision reversal, stale worker delivery, receipts, invitation
and rescheduling contents, calendar identifiers, incomplete release validation,
private applicant screens, custom template preservation and update-check defaults.
The final regression run passed **45 tests**.

These adapters target pretalx 2026.2.1; re-review the integration seams and rerun
the tests before updating upstream. Python/domain safeguards do not restrict a
host/database administrator or raw SQL. Staff can still download documents they
are authorised to read and edit email copy. Some upstream organiser labels and
public-schedule menu items remain visible, although the privacy flags and export
routes prevent publication. Applying pending decisions makes the result visible
to applicants before its draft email is sent; organisers should release decisions
and send the reviewed outbox together.

The [production guide](../deployment/PRODUCTION.md) contains the remaining
host/proxy deployment checks. This review does not substitute for testing the
actual public HTTPS endpoint. Post-interview decisions remain manual.

The [email defaults](EMAIL_TEMPLATES_DRAFT.md) are available for wording review.
Adjustment replies go to the recruitment mailbox, not the application database;
that mailbox still needs appropriate access and retention controls.
