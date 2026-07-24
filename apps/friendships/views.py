from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import StudentProfile
from apps.accounts.serializers import StudentProfileSerializer
from apps.friendships.models import Friendship
from apps.friendships.serializers import FriendshipSerializer, SendFriendRequestSerializer, \
    RespondFriendRequestSerializer, RemoveFriendSerializer
from apps.friendships import services as services

class FriendshipView(APIView):
    permission_classes = (IsAuthenticated,)

    #sender user requests friendship
    @extend_schema(request = SendFriendRequestSerializer, responses = FriendshipSerializer)
    def post(self, request:Request) -> Response:
        body = SendFriendRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        sender = StudentProfile.objects.get(user=request.user)
        receiver = StudentProfile.objects.get(id = body.validated_data["received_id"])

        friendship = services.send_request(sender, receiver)

        return Response(FriendshipSerializer(friendship).data, status=201)

    #receiver user changes the status ()accept/reject
    @extend_schema(request = RespondFriendRequestSerializer, responses=FriendshipSerializer)
    def patch(self, request: Request) -> Response:
        body = RespondFriendRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        actor = StudentProfile.objects.get(user=request.user)
        friendship_id = body.validated_data["id"]
        action = body.validated_data["action"]

        if action == "accept":
            friendship = services.accept_request(friendship_id, actor.id)
        else:
            friendship = services.reject_request(friendship_id, actor.id)

        return Response(FriendshipSerializer(friendship).data)


class RemoveFriendView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(request=RemoveFriendSerializer, responses=None)
    def delete(self, request: Request) -> Response:
        body = RemoveFriendSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        profile = StudentProfile.objects.get(user=request.user)
        services.remove_friend(profile.id, body.validated_data["other_id"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class ListFriendsView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(responses=StudentProfileSerializer(many=True))
    def get(self, request: Request) -> Response:
        profile = StudentProfile.objects.get(user=request.user)
        friends = services.list_friends(profile.id)
        return Response(StudentProfileSerializer(friends, many=True).data)


class ListFriendshipRequestsView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(responses=FriendshipSerializer(many=True))
    def get(self, request: Request) -> Response:
        profile = StudentProfile.objects.get(user=request.user)
        pending = services.list_friendship_requests(profile.id)
        return Response(FriendshipSerializer(pending, many=True).data)


