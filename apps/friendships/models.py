from django.db import models


class Friendship(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    from_profile = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="sent_requests")
    to_profile = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name="received_requests")
    status = models.CharField(choices=Status.choices, default=Status.PENDING, max_length=10)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["from_profile", "to_profile"],
                name="unique_friendship_pair",
            ),

            models.CheckConstraint(
                check=~models.Q(from_profile=models.F("to_profile")),
                name="no_self_friendship"
            )
        ]

    def __str__(self):
        return f"{self.from_profile} -> {self.to_profile} ({self.status})"