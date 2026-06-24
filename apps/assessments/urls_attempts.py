from django.urls import path

from .views import (
    AttemptAnswerView,
    AttemptCreateView,
    AttemptFinishView,
    AttemptReviewView,
    AttemptTutorView,
)

app_name = "attempts"

urlpatterns = [
    path("", AttemptCreateView.as_view(), name="attempt-create"),
    path("<int:id>/answer/", AttemptAnswerView.as_view(), name="attempt-answer"),
    path("<int:id>/finish/", AttemptFinishView.as_view(), name="attempt-finish"),
    path("<int:id>/review/", AttemptReviewView.as_view(), name="attempt-review"),
    path("<int:id>/tutor/", AttemptTutorView.as_view(), name="attempt-tutor"),
]
