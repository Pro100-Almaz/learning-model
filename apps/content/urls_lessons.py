from django.urls import path

from apps.content.views import LessonDetailView

app_name = "lessons"

urlpatterns = [
    path("<int:lesson_id>/", LessonDetailView.as_view(), name="lesson-detail"),
]
