from django.urls import path

from .views import download

urlpatterns = [path("arc-files/<path:name>", download, name="download")]


from .manual_mail import mailbox, download_message

urlpatterns += [
    path("orga/manual-mail/", mailbox, name="manual_mail"),
    path("orga/manual-mail/<uuid:message_id>/", mailbox, name="manual_mail_detail"),
    path("orga/manual-mail/<uuid:message_id>/download/", download_message, name="manual_mail_download"),
]
