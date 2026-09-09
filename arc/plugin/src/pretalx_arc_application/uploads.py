"""Validate recruitment documents both in forms and before private file storage."""
import re
from pathlib import Path
from django.core.exceptions import ValidationError
from django.db.models.fields.files import FieldFile

PDF_QUESTIONS = {"arc_cv", "arc_cover_letter", "arc_degree_evidence"}
PDF_LIMIT = 10 * 1024 * 1024


def validate_pdf(upload, *, name=None):
    if not upload:
        return
    if Path(name or upload.name or "").suffix.lower() != ".pdf":
        raise ValidationError("Upload a PDF document.")
    if getattr(upload, "content_type", None) not in (None, "application/pdf", "application/octet-stream"):
        raise ValidationError("The uploaded document must be a PDF.")
    position = upload.tell()
    try:
        upload.seek(0, 2)
        if upload.tell() > PDF_LIMIT:
            raise ValidationError("Each PDF must be no larger than 10 MiB.")
        upload.seek(0)
        if not re.match(rb"%PDF-(?:1\.[0-7]|2\.0)(?:\s|$)", upload.read(16)):
            raise ValidationError("The document does not have a valid PDF signature.")
    finally:
        upload.seek(position)


class RecruitmentDocumentFile(FieldFile):
    def save(self, name, content, save=True):
        if self.instance.question.identifier in PDF_QUESTIONS:
            validate_pdf(content, name=name)
        return super().save(name, content, save=save)


def install_upload_validation():
    from pretalx.submission.models import Answer
    from pretalx.submission.interfaces.forms import question as forms
    from pretalx.common.forms.fields import ExtensionFileField
    from .adapters import replace_function
    Answer._meta.get_field("answer_file").attr_class = RecruitmentDocumentFile
    original = forms._build_file
    def build_file(*, question, initial, help_text, **kwargs):
        if question.identifier not in PDF_QUESTIONS:
            return original(initial=initial, help_text=help_text, **kwargs)
        return ExtensionFileField(initial=initial, help_text=help_text, extensions={".pdf": ("application/pdf",)}, max_size=PDF_LIMIT, validators=[validate_pdf])
    # The builder registry keeps a reference to the original function.
    replace_function(forms, "_build_file", build_file)
    for key, value in forms._BUILDERS.items():
        if value is original:
            forms._BUILDERS[key] = build_file
