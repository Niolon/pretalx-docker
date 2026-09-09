# Recruitment privacy launch and operations

This plugin provides live-system controls. Durham must approve the actual notice,
lawful basis, retention period and hosting arrangements before recruitment opens.
It does not determine those policy values or provide legal approval.

## Configure a call

1. Enable the recruitment plugin. The default form requires a CV, cover letter,
   evidence of the required degree and the privacy acknowledgement. A project
   link is optional. Referee information, recommendation letters and adjustment
   information are not requested at this stage.
2. Open **Settings → Recruitment privacy**. Enter the approved lawful basis,
   retention period in days, and the hosting information, including backup and
   infrastructure-log expiry. Confirm approval only after Durham has approved
   the notice and these values. Use the call's contact address for recruitment.
3. Review the applicant-facing notice at `/<call>/recruitment/privacy/`.
   The fixed draft is in the plugin's `templates/recruitment/privacy.html`.
   It identifies Durham as controller and describes purposes, data, access,
   Advanced Web Hosting, rights/contact, withdrawal, retention, human decisions
   and the separate formal PGR admissions process. The configured policy values
   are escaped text. There is no arbitrary HTML editor or notice-version system.
4. Check the landing page, submission steps, footer and receipt template links.
   The acknowledgement records reading the notice; it is not consent.
5. Use a dedicated reviewer team limited to this call. Assign applications under
   the existing review assignment screen. Review phases default to assigned
   applications and do not grant decision powers. Avoid adding reviewer accounts
   to organiser teams: pretalx combines permissions across memberships.
6. Run `python -m pretalx arc_privacy_check --strict --event <call>` before opening.
   Run without `--event` to check the whole recruitment instance. The command is
   read-only, prints actionable failures, and returns a non-zero status on strict
   failure. Missing policy approval or completion of recruitment blocks browser
   intake (including access codes) and submission-domain creation/submission.

Do not use example/test policy values as Durham approval. The readiness command
can check recorded configuration, not authenticate approval. Verify the proxy,
private volume access, hosting contracts, backup/log expiry and mail-provider
retention separately using the production guide. Treat strict readiness as a
release gate; repeat it after changes to review permissions, forms or policies.

## Withdrawal and erasure

Applicants can permanently withdraw throughout recruitment, including after
shortlisting and scheduling. The confirmation page requires ownership, POST,
CSRF protection and an explicit checkbox. The ordinary edit page also links to it.

Ordinary immediate or pending state changes cannot silently erase an application;
use the explicit withdrawal confirmation or the organiser deletion action.

The shared erasure path removes the application, answers/files, review records,
assignments, interview slots across versions, application activity records and
associated stored correspondence. Technical application titles use codes, not
names. Manual-mail ownership survives deletion of the source outbox message.
An email containing several applications is removed in full; retention output
reports the shared-message count so unrelated correspondence can be regenerated.

A user with another application or an organiser/reviewer role retains their
account and unrelated records. An applicant-only account with no remaining
purpose is removed; deleted accounts cannot authenticate. External emails,
downloaded documents and calendars cannot be recalled. Backups and infrastructure
logs expire under the approved hosting policy, not this command.

Delivery workers receive database IDs, not copies of recruitment message bodies.
Erasure and delivery lock users, applications and mail in the same order. If a
message finishes delivery before erasure gets the lock, it cannot be recalled;
a worker that runs after erasure must find no message to deliver or recreate.
System/account messages are stored without an event, so their credential links
remain outside ordinary call-organiser outboxes.

Private files are removed after database commit using the existing storage hooks.
If a filesystem failure prevents removal, a small durable retry record remains;
strict readiness fails and the UI does not claim complete file cleanup. Correct
storage permissions/access and run:

```sh
python -m pretalx arc_retention_sweep --event <call> --retry-file-deletions --execute
```

This retries only previously requested private-file deletions, including those
from withdrawal on an otherwise active call. It does not delete applications.

## Retention

Record the actual **Recruitment completion date** in the privacy settings. This
closes intake and starts the approved retention clock. Dates of interviews,
individual decisions and submission deadlines are not substitutes.

```sh
python -m pretalx arc_retention_sweep --event <call> --before YYYY-MM-DD --dry-run
python -m pretalx arc_retention_sweep --event <call> --before YYYY-MM-DD --execute
```

The cutoff must not be in the future. A call is eligible only when completion
plus its approved retention days falls strictly before the cutoff. Unapproved
policies and calls without completion dates are refused. Without `--execute`,
the command is a dry run. Repeating a completed sweep is safe. Output contains
counts, not applicant identities or email contents. Errors require operator
attention; unresolved file cleanup returns failure and can be retried.

Run this manually or through the hosting platform's scheduler. Record successful
runs and investigate failures. Coordinate any mailbox copies held outside the
application and backup/log expiry with the approved institutional policies.
There is no scheduler, retention dashboard or automatic admissions transfer.

## Validation evidence and CI

The `Recruitment privacy tests` GitHub job installs the pinned pretalx submodule
and plugin, and runs the regression suite against disposable PostgreSQL. Tests
block external SMTP connections and use temporary document storage. They run
`arc_privacy_check --strict` against both approved synthetic and incomplete
fixtures. PostgreSQL tests verify both delivery-first and erasure-first races;
SQLite cannot establish that locking guarantee.

Image publishing depends on that job. Configure repository branch protection to
require **Recruitment privacy tests** as well; workflow YAML cannot enforce branch
protection by itself. Keep the passing test output and the actual deployment's
strict readiness output with the draft/approved notice and DPIA evidence.
