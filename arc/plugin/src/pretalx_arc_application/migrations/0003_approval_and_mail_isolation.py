from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


RELATIONS = (
    ('mail', 'QueuedMail', 'submissions', 'recruitment_one_app_queued'),
    ('pretalx_arc_application', 'MailSubjects', 'submissions', 'recruitment_one_app_subjects'),
    ('pretalx_arc_application', 'ManualMail', 'subject_submissions', 'recruitment_one_app_manual'),
)


def isolate(apps, schema_editor):
    from django.db.models import Count
    for app, name, field, index in RELATIONS:
        parent = apps.get_model(app, name)
        through = parent._meta.get_field(field).remote_field.through
        parent_field = next(f for f in through._meta.fields if f.is_relation and f.remote_field.model == parent)
        if through.objects.values(parent_field.attname).annotate(n=Count('pk')).filter(n__gt=1).exists():
            raise RuntimeError('Shared application mail exists. Split it into individually rendered messages before migrating; no correspondence has been deleted.')
        quote = schema_editor.quote_name
        schema_editor.execute(f'CREATE UNIQUE INDEX {quote(index)} ON {quote(through._meta.db_table)} ({quote(parent_field.column)})')
    # Previous booleans contain no approval provenance and cannot count as approval.
    apps.get_model('pretalx_arc_application', 'RecruitmentPolicy').objects.update(approved=False)


def reverse_isolation(apps, schema_editor):
    for _, _, _, index in RELATIONS:
        schema_editor.execute(f'DROP INDEX {schema_editor.quote_name(index)}')


class Migration(migrations.Migration):
    dependencies = [
        ('pretalx_arc_application', '0002_mailsubjects_recruitmentpolicy'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(model_name='recruitmentpolicy', name='approval_reference', field=models.CharField(max_length=500, blank=True, default=''), preserve_default=False),
        migrations.AddField(model_name='recruitmentpolicy', name='approved_at', field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name='recruitmentpolicy', name='approved_by', field=models.ForeignKey(to=settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, related_name='recruitment_policy_approvals')),
        migrations.RunPython(isolate, reverse_isolation),
    ]
