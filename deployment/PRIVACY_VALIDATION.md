# Recruitment privacy validation — 8 September 2026

Tested against the repository's pretalx 2026.2.1 revision and PostgreSQL 15.
The full local PostgreSQL suite passed **83 tests**, including actual concurrent
worker-delivery/erasure tests in both execution orders. CI is configured to run
this suite before image publishing; a remote GitHub run has not been performed
as part of this local implementation.

The reset development instance completed this synthetic workflow:

- Created a public recruitment advert and reviewed notice links.
- Temporarily configured explicitly synthetic policy values; strict readiness passed.
- Submitted two applications through the browser-request wizard, with CV,
  cover letter, required degree evidence and acknowledgement.
- Captured both receipt emails in the protected mailbox. Pretalx schedules
  initial receipts with an approximately one-minute delay before dispatch.
- Assigned a reviewer and captured a separate reviewer-team invitation.
- Shortlisted one applicant and sent the other a pre-interview rejection.
- Finalised private interview arrangements and captured an invitation with a calendar.
- Withdrew the shortlisted application through the applicant confirmation action.
- Ran retention in dry-run mode, confirmed no changes, then erased the expired
  remaining application and its applicant-only account and stored correspondence.
- Removed synthetic policy approval. Actual dev strict readiness now fails on
  missing Durham approval, and new application submission is closed.

The live walkthrough identified a calendar precision edge case: slot timestamps
with microseconds did not compare equal to their serialized calendar values.
Validation now compares the serialized representation, covered by regression.

Development remains at `http://localhost:8088/privacy-demo/`. Mailpit remains
available at `http://localhost:8026`, but empty server SMTP configuration selects
the protected **Manual delivery** screen. No external email was sent.

The reset administrator credentials are in the owner-readable local file
`/tmp/recruitment-development/recruitment-dev-credentials.txt`, outside git.
The remaining reviewer invitation and all notice values are synthetic.

This evidence verifies application behaviour, not legal approval or the hosting
panel's proxy, backup, infrastructure-log or mail-provider retention controls.
Durham approval and external hosting verification remain prerequisites for launch.
