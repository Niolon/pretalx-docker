# Manual email delivery

Manual delivery activates automatically when the selected SMTP host is empty.
Set `PRETALX_MAIL_HOST=""` (or leave `host` empty in `[mail]`) on the server
when no SMTP service is available. There is no separate manual-mode switch.
The development overlay leaves this host empty.

Configure the server once to send recruitment messages, reviewer invitations and
account emails through the same mail account. For example, in the server's private
`pretalx.cfg`:

```ini
[mail]
from = recruitment@your-domain.example
host = smtp.your-provider.example
port = 587
user = recruitment@your-domain.example
password = YOUR_PROVIDER_APP_PASSWORD
# Use the port and TLS/SSL settings required by your provider.
tls = True
ssl = False
```

An email address alone cannot authorise sending: the provider must permit SMTP
access with these credentials, or provide an authorised relay. Keep credentials
out of git. Set the call's contact/reply-to address to the recruitment address so
applicant replies reach that inbox. Leave custom SMTP disabled on the call to use
the server account. Manual delivery does not receive replies.

Environment equivalents are `PRETALX_MAIL_FROM`, `PRETALX_MAIL_HOST`,
`PRETALX_MAIL_PORT`, `PRETALX_MAIL_USER`, `PRETALX_MAIL_PASSWORD`,
`PRETALX_MAIL_TLS` and `PRETALX_MAIL_SSL`. Environment settings override the config
file; remove the dev overlay's empty host override before enabling server SMTP.
Run with `PRETALX_DEBUG=False`, and restart all web and worker processes after
changing server settings. An explicitly configured `localhost` host counts as
SMTP configuration, for installations using a local relay.

Calls with custom SMTP enabled use their own host; an empty custom host selects
manual delivery for that call. Account and reviewer-invitation emails always use
the server settings. The SMTP test action uses the selected custom settings too.
A configured but unreachable SMTP server or rejected credentials produce normal
mail errors, **not** a silent switch to manual delivery.

Use the recruitment plugin image and run its startup migrations. The mailbox
requires its `ManualMail` table and the existing private data volume.

## Preparing and handing over an email

1. Review applicant messages in the normal call outbox. The existing Send action
   prepares the email in manual mode; it does not deliver it to the recipient.
   Automatic receipts, reviewer invitations and account emails are also captured.
2. Open **Manual delivery** in the organiser navigation, or `/orga/manual-mail/`.
3. Open the message and check its subject and To/CC/BCC recipients. Copy its text
   into your email client or download the `.eml` file to retain attachments such
   as the interview calendar. Add BCC recipients separately when using the file.
4. Pass the email on using your chosen mail environment, then click **Mark as
   handed over**. The mailbox records which administrator marked it and when;
   this is a manual record, not delivery confirmation from a mail provider.

Messages initially say **Awaiting manual delivery**. The upstream call's “Sent”
history records that its message was prepared successfully; use the manual mailbox
for handover status. A banner indicates when the current call uses manual delivery. Mailpit is not needed for new messages in this mode; the existing
dev Mailpit service is retained for previous test emails.

For already prepared shortlist/rejection or interview messages, collection is
blocked if the source decision or released interview details have become stale.
Prepare a new draft after correcting the decision or interview arrangements.
Copies already downloaded cannot be recalled.

## Access and retention

Only an active instance administrator can use this instance-wide mailbox. Ordinary
organiser-team membership and reviewer permissions do not grant access: account
emails and reviewer invitations contain authentication links and cannot safely
be shared with every organiser. The mailbox displays plain text, not active email
HTML. Responses disable caching; email files live under `/data/arc-private/manual-mail`,
with no public media URL. The database stores the message's plain text, recipients
and handover metadata. Include both the database and private data volume in the
existing access, backup and retention policy. Deleting a `ManualMail` row also
removes its private email file after the database transaction commits.

Password-reset and invitation links retain their normal expiry and single-use
behaviour. Pass them on promptly, and use the normal reissue action when needed.
Manual mode does not provide a recipient inbox or receive replies: recipients
should reply to the recruitment address configured on the call.

Configuring SMTP enables automatic delivery for new messages after restart.
Previously prepared messages remain accessible in **Manual delivery** and are
not sent automatically; complete their handover separately.

Application withdrawal and retention also erase associated manual copies, even
when their source outbox row has already been removed. See the
[privacy operations guide](RECRUITMENT_PRIVACY.md). System/account messages retain
explicit account ownership and remain inaccessible to ordinary call organisers.
