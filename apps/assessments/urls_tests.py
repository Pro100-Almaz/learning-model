from django.urls import path

from .views import TestDetailView

app_name = "tests"

urlpatterns = [
    path("<int:id>/", TestDetailView.as_view(), name="test-detail"),
]
