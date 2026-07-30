from rest_framework import serializers

from apps.accounts.models import StudentProfile
from apps.friendships.models import Friendship


class FriendProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentProfile
        fields = ["id", "username"]


class FriendshipSerializer( serializers.ModelSerializer):
    from_profile = FriendProfileSerializer(read_only=True)
    to_profile = FriendProfileSerializer(read_only=True)

    class Meta:
        model = Friendship
        fields = ["id", "from_profile", "to_profile", "status", "created_at", "updated_at"]


class SendFriendRequestSerializer(serializers.Serializer):
    receiver_id = serializers.IntegerField()

class RespondFriendRequestSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    action = serializers.ChoiceField(choices=["accept",  "reject"])


class CancelFriendRequestSerializer(serializers.Serializer):
    id = serializers.IntegerField()


class RemoveFriendSerializer(serializers.Serializer):
    to_remove_id = serializers.IntegerField()
