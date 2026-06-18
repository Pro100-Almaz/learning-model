"""URL routes for analytics endpoints.

Mounted under /api/v1/analytics/ by conf/urls.py.
"""

from django.urls import path

from .views import RecommendationsView, TagStatsView

app_name = "analytics"

urlpatterns = [
    path("tags/", TagStatsView.as_view(), name="tags"),
    path("recommendations/", RecommendationsView.as_view(), name="recommendations"),
]
