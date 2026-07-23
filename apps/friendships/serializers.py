from rest_framework import serializers

from apps.accounts.serializers import StudentProfileSerializer
from apps.friendships.models import Friendship


class FriendshipSerializer( serializers.ModelSerializer):
    from_profile = StudentProfileSerializer(read_only=True)
    to_profile = StudentProfileSerializer(read_only=True)

    class Meta:
        model = Friendship
        fields = ["id", "from_profile", "to_profile", "status", "created_at", "updated_at"]


class SendFriendRequestSerializer(serializers.Serializer):
    receiver_id = serializers.IntegerField()

class RespondFriendRequestSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    action = serializers.ChoiceField(choices=["accept",  "reject"])


class RemoveFriendSerializer(serializers.Serializer):
    pass