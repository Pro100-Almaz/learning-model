from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import StudentProfile
from apps.friendships.models import Friendship
from apps.friendships.serializers import CancelFriendRequestSerializer, FriendProfileSerializer, \
    FriendshipSerializer, SendFriendRequestSerializer, RespondFriendRequestSerializer
from apps.friendships import services as services

class FriendshipView(APIView):
    permission_classes = (IsAuthenticated,)

    #sender user requests friendship
    @extend_schema(tags=["Friendships"], request = SendFriendRequestSerializer, responses = FriendshipSerializer)
    def post(self, request:Request) -> Response:
        body = SendFriendRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        sender = get_object_or_404(StudentProfile, user=request.user)
        receiver = get_object_or_404(StudentProfile, id=body.validated_data["receiver_id"])

        # FriendshipError means the caller asked for something invalid ("already
        # friends", "cannot be friends with yourself") — a 400, not a 500.
        try:
            friendship = services.send_request(sender, receiver)
        except services.FriendshipError as exc:
            raise ValidationError({"detail": str(exc)})

        return Response(FriendshipSerializer(friendship).data, status=status.HTTP_201_CREATED)

    #receiver user changes the status ()accept/reject
    @extend_schema(tags=["Friendships"], request = RespondFriendRequestSerializer, responses=FriendshipSerializer)
    def patch(self, request: Request) -> Response:
        body = RespondFriendRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        actor = get_object_or_404(StudentProfile, user=request.user)
        friendship_id = body.validated_data["id"]
        action = body.validated_data["action"]

        try:
            if action == "accept":
                friendship = services.accept_request(friendship_id, actor.id)
            else:
                friendship = services.reject_request(friendship_id, actor.id)
        except Friendship.DoesNotExist:
            raise NotFound("Friendship request not found.")
        except services.FriendshipError as exc:
            raise ValidationError({"detail": str(exc)})

        return Response(FriendshipSerializer(friendship).data)

    #sender user withdraws a request the receiver hasn't answered yet
    @extend_schema(tags=["Friendships"], request = CancelFriendRequestSerializer, responses={204: None})
    def delete(self, request: Request) -> Response:
        body = CancelFriendRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        actor = get_object_or_404(StudentProfile, user=request.user)

        try:
            services.cancel_request(body.validated_data["id"], actor.id)
        except Friendship.DoesNotExist:
            raise NotFound("Friendship request not found.")
        except services.FriendshipError as exc:
            raise ValidationError({"detail": str(exc)})

        return Response(status=status.HTTP_204_NO_CONTENT)


class ListFriendsView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(tags=["Friendships"], responses=FriendProfileSerializer(many=True))
    def get(self, request: Request, profile_id: int) -> Response:
        friends = services.list_friends(profile_id)
        return Response(FriendProfileSerializer(friends, many=True).data)


class ListFriendshipRequestsView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        tags=["Friendships"],
        parameters=[
            OpenApiParameter(
                name="direction",
                type=str,
                enum=["received", "sent"],
                required=False,
                description=(
                    "'received' (default) lists requests awaiting this profile's "
                    "answer; 'sent' lists the requests it is still waiting on."
                ),
            )
        ],
        responses=FriendshipSerializer(many=True),
    )
    def get(self, request: Request, profile_id: int) -> Response:
        direction = request.query_params.get("direction", "received")
        if direction not in ("received", "sent"):
            raise ValidationError({"direction": 'Must be "received" or "sent".'})

        if direction == "sent":
            pending = services.list_sent_friendship_requests(profile_id)
        else:
            pending = services.list_friendship_requests(profile_id)

        return Response(FriendshipSerializer(pending, many=True).data)
