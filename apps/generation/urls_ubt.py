"""Routes for the UBT blueprint engine, mounted at /api/v1/ubt/.

Kept in their own module rather than appended to generation/urls.py: those
endpoints are all `jobs/<id>/...` for the MAIQE batch pipeline, and these have
no job at all. Two URL files make that difference visible from the router.
"""

from django.urls import path

from apps.generation.ubt_views import (
    UbtCoverageView,
    UbtPreviewView,
    UbtPublishView,
    UbtTopicListView,
)

app_name = "ubt"

urlpatterns = [
    path("topics/", UbtTopicListView.as_view(), name="ubt-topics"),
    path("preview/", UbtPreviewView.as_view(), name="ubt-preview"),
    path("questions/", UbtPublishView.as_view(), name="ubt-questions"),
    path("coverage/", UbtCoverageView.as_view(), name="ubt-coverage"),
]
