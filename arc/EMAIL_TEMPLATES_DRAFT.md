# Recruitment email defaults — wording review

These defaults are now installed by the plugin for new calls. Each call’s email
templates remain editable; saved customisations are not overwritten. Braced values
below are actual pretalx placeholders, filled automatically. `{event_name}` is the
individual recruitment call’s configured name, so there is no fixed organisation
in the sign-off. Post-interview decisions remain manual.

## Receipt

**Subject:** `Application received — {submission_type}`

```text
Hello {name},

Thank you for applying for {submission_type}. We have received your application.

You can view your application here:
{submission_url}

We will contact you when shortlisting is complete. If you have any questions, please reply to this email.

Kind regards,
The {event_name} recruitment team
```

## Shortlist

**Subject:** `You have been shortlisted — {submission_type}`

```text
Hello {name},

We are pleased to let you know that you have been shortlisted for {submission_type}.

Please use the following link to confirm that you would like to proceed to interview:
{confirmation_link}

We will send your interview date, time and joining instructions separately. If you no longer wish to proceed, please reply to let us know.

Kind regards,
The {event_name} recruitment team
```

## Pre-interview rejection

**Subject:** `Update on your application — {submission_type}`

```text
Hello {name},

Thank you for your interest in {submission_type} and for the time you put into your application.

After reviewing applications, we are sorry to let you know that we will not be inviting you to interview on this occasion.

We wish you all the best with your future applications.

Kind regards,
The {event_name} recruitment team
```

## Interview invitation or update

**Subject:** `{interview_subject} — {event_name}`

```text
Hello {name},

{interview_opening}

{interview_details}

Please reply if this time is unsuitable or if you need any adjustments to take part. Please tell us what would help you participate; you do not need to provide a diagnosis or medical history.

We look forward to meeting you.

Kind regards,
The {event_name} recruitment team
```

## Interview fields

`{interview_subject}` becomes “Your interview details” for an initial invitation
or “Updated interview details” for a changed slot. The opening likewise explains
whether this is an invitation or replaces earlier arrangements.

`{interview_details}` includes the position, weekday and full date, start and end
time, timezone abbreviation, UTC offset and timezone name, room, joining
instructions and authenticated application link. For example:

```text
Date: Tuesday, 20 October 2026
Time: 10:00–10:30 BST (UTC+0100; Europe/London)
Location: Interview panel A

Join https://meet.example.test/panel-a
```

The calendar attachment contains the same details, an authenticated application
link, a stable interview identifier and a sequence number for updates. Neither
receipt nor shortlist/rejection email requests adjustments. Only invited people
are asked to reply to the recruitment mailbox; their replies are not application
answers. Receipt emails contain no appended application answers or document links.

To change the team name independently of the call name, edit the sign-off in that
call’s templates. No extra global setting is required.
