"""URL routes for analytics endpoints.

Mounted under /api/v1/analytics/ by conf/urls.py.
"""

from django.urls import path

from apps.analytics.views import RecommendationsView, TagStatsView, StudentReportView

app_name = "analytics"

urlpatterns = [
    path("tags/", TagStatsView.as_view(), name="tags"),
    path("recommendations/", RecommendationsView.as_view(), name="recommendations"),
    path("report/", StudentReportView.as_view(), name="report"),
]

