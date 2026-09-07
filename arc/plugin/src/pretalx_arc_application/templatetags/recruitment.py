from django import template

register = template.Library()


@register.simple_tag
def private_interviews(submission, user):
    from pretalx_arc_application.emails import slot_details
    from pretalx.common.exceptions import SendMailException

    if not user.is_authenticated or not user.is_active or not submission.speakers.filter(user=user).exists():
        return []
    if submission.state not in {"accepted", "confirmed"} or not submission.event.current_schedule:
        return []
    result = []
    for slot in submission.slots.filter(schedule=submission.event.current_schedule):
        try:
            result.append(slot_details(slot))
        except SendMailException:
            continue
    return result
