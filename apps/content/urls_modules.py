from django.urls import path

from apps.content.views import LessonsListView

app_name = "modules"

urlpatterns = [
    path("<int:module_id>/", LessonsListView.as_view(), name="module-detail"),
]
