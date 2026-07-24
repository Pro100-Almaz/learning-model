from django.db import transactions
from django.db.models import Q
from apps.accounts.models import StudentProfile
from apps.accounts.serializers import StudentProfileSerializer
from apps.friendships.models import Friendship

class FriendshipError(Exception):
    pass


@transactions.atomic
def send_request(sender: StudentProfile, receiver: StudentProfile) -> Friendship:
    if sender == receiver:
        return FriendshipError("Cannot be friends with yourself")

    reverse = Friendship.objects.filter(
        from_profile=receiver,
        to_profile=sender,
        status=Friendship.Status.PENDING
    )
    if reverse:
        reverse.status = Friendship.Status.ACCEPTED
        reverse.save()
        return reverse

    friendship, created = Friendship.objects.get_or_create(
        from_profile=sender,
        to_profile=receiver,
        defaults = {
            "status": Friendship.Status.PENDING,
        }
    )

    if not created:
        if friendship.status == Friendship.Status.REJECTED:
            friendship.status = Friendship.Status.PENDING
            friendship.save(update_field=["status", "updated_at"])
        elif friendship.status == Friendship.Status.ACCEPTED:
            raise FriendshipError("Already friends")
        return friendship


def accept_request(friendship_id: int, actor_id: int) -> Friendship:
    friendship = Friendship.objects.get(id=friendship_id)
    if friendship.to_profile_id != actor_id:
        raise FriendshipError("Wrong receiver")
    friendship.status = Friendship.Status.ACCEPTED
    friendship.save(updated_fields=["status", "updated_at"])
    return friendship


def reject_request(friendship_id: int, actor_id: int) -> Friendship:
    friendship = Friendship.objects.get(id=friendship_id)
    if friendship.to_profile_id != actor_id:
        raise FriendshipError("Wrong receiver")
    friendship.status = Friendship.Status.REJECTED
    friendship.save(update_fields=["status", "updated_at"])
    return friendship

def remove_friend(profile_id: int, other_id: int) -> None:
    Friendship.objects.filter(
        (Q(from_profile_id=profile_id, to_profile_id=other_id) |
         Q(from_profile_id=other_id, to_profile_id=profile_id)),
        status=Friendship.Status.ACCEPTED,
    ).delete()

def list_friends(profile_id: int) -> list[StudentProfile]:
    friendships = Friendship.objects.filter(
        (Q(from_profile_id=profile_id) | Q(to_profile_id=profile_id)),
        status=Friendship.Status.ACCEPTED,
    )
    friends_list = []
    for friend in friendships:
        if friend.to_profile_id != profile_id:
            friends_list.append(friend.to_profile)
        else:
            friends_list.append(friend.from_profile)
    return friends_list

def list_friendship_requests(receiver_id: int) -> list[Friendship]:
    return list(Friendship.objects.filter(
        to_profile_id = receiver_id,
        status=Friendship.Status.PENDING,
    ))


