from django.urls import path

from apps.roadmap.views import DiagnosticInfoView, RoadmapRegenerateView, RoadmapView

app_name = "roadmap"

urlpatterns = [
    path("", RoadmapView.as_view(), name="detail"),
    path("diagnostic/", DiagnosticInfoView.as_view(), name="diagnostic"),
    path("regenerate/", RoadmapRegenerateView.as_view(), name="regenerate"),
]
