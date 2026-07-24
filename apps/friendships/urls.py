from django.urls import path

from apps.friendships import views

urlpatterns = [
      path("friendships/", views.FriendshipView.as_view()),
      path("friendships/remove/", views.RemoveFriendView.as_view()),
      path("friendships/friends/", views.ListFriendsView.as_view()),
      path("friendships/requests/", views.ListFriendshipRequestsView.as_view()),
  ]
