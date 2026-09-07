from django.urls import path

from .views import download

urlpatterns = [path("arc-files/<path:name>", download, name="download")]
