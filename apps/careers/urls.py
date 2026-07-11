from django.urls import path

from apps.careers.views import GrantCalculateView, UniversityListView

app_name = "careers"

urlpatterns = [
    path("universities/", UniversityListView.as_view(), name="universities"),
    path("calculate/", GrantCalculateView.as_view(), name="calculate"),
]
