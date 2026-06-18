from django.urls import path

from .views import LessonDetailView

app_name = "lessons"

urlpatterns = [
    path("<int:id>/", LessonDetailView.as_view(), name="lesson-detail"),
]
