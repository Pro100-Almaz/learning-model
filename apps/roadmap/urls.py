from django.urls import path

from .views import (
    ChapterLadderNextView,
    ChapterLadderStartView,
    DiagnosticInfoView,
    RoadmapRegenerateView,
    RoadmapView,
)

app_name = "roadmap"

urlpatterns = [
    path("", RoadmapView.as_view(), name="detail"),
    path("diagnostic/", DiagnosticInfoView.as_view(), name="diagnostic"),
    path("regenerate/", RoadmapRegenerateView.as_view(), name="regenerate"),
    path(
        "chapter/<int:module_id>/ladder/start/",
        ChapterLadderStartView.as_view(),
        name="chapter-ladder-start",
    ),
    path(
        "chapter/ladder/next/",
        ChapterLadderNextView.as_view(),
        name="chapter-ladder-next",
    ),
]
