from django.urls import path

from apps.content.views import ModuleListView

app_name = "classes"

urlpatterns = [
    path("<int:class_grade_id>/", ModuleListView.as_view(), name="module-list"),
]