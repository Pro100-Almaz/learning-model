from django.urls import path

from .views import GamificationMeView

app_name = "gamification"

urlpatterns = [
    path("me/", GamificationMeView.as_view(), name="me"),
]
