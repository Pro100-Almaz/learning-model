from django.urls import path

from .views import ModuleDetailView, ModuleListView

app_name = "modules"

urlpatterns = [
    path("", ModuleListView.as_view(), name="module-list"),
    path("<int:id>/", ModuleDetailView.as_view(), name="module-detail"),
]
