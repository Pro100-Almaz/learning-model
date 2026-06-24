from django.urls import path

from .views import JobCancelView, JobDetailView, JobListCreateView, JobStreamView

app_name = "generation"

urlpatterns = [
    path("jobs/", JobListCreateView.as_view(), name="job-list"),
    path("jobs/<int:id>/", JobDetailView.as_view(), name="job-detail"),
    path("jobs/<int:id>/stream/", JobStreamView.as_view(), name="job-stream"),
    path("jobs/<int:id>/cancel/", JobCancelView.as_view(), name="job-cancel"),
]
