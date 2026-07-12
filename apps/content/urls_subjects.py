from django.urls import path

from apps.content.views import SubjectListView, SubjectListAllView, ClassGradeListView

app_name = "subjects"

urlpatterns = [
    path("", SubjectListView.as_view(), name="subject-list"),
    path("all/", SubjectListAllView.as_view(), name="subject-list-all"),
    path("<int:subject_id>/", ClassGradeListView.as_view(), name="class-list"),
]
