from django.urls import path

from apps.content.views import LessonDetailView, NextLessonsView

app_name = "lessons"

urlpatterns = [
    path("<int:lesson_id>/", LessonDetailView.as_view(), name="lesson-detail"),
    path("next_lessons/", NextLessonsView.as_view(), name="next-lessons"),
]
