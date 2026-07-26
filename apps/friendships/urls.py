from django.urls import path

from apps.friendships import views

urlpatterns = [
      path("", views.FriendshipView.as_view()),
      path("friends/<int:profile_id>/", views.ListFriendsView.as_view()),
      path("requests/<int:profile_id>/", views.ListFriendshipRequestsView.as_view()),
  ]
