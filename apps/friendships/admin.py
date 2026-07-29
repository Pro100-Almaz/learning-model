"""Django admin registration for the friendships app."""

from django.contrib import admin, messages
from django.utils import timezone

from apps.friendships.models import Friendship


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ("id", "from_profile", "to_profile", "status", "created_at", "updated_at")
    list_filter = ("status", "created_at")
    search_fields = (
        "from_profile__user__email",
        "from_profile__username",
        "to_profile__user__email",
        "to_profile__username",
    )
    list_select_related = ("from_profile__user", "to_profile__user")
    autocomplete_fields = ("from_profile", "to_profile")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    actions = ("mark_accepted", "mark_rejected", "mark_pending")

    @admin.action(description="Mark selected friendships as accepted")
    def mark_accepted(self, request, queryset):
        self._set_status(request, queryset, Friendship.Status.ACCEPTED)

    @admin.action(description="Mark selected friendships as rejected")
    def mark_rejected(self, request, queryset):
        self._set_status(request, queryset, Friendship.Status.REJECTED)

    @admin.action(description="Mark selected friendships as pending")
    def mark_pending(self, request, queryset):
        self._set_status(request, queryset, Friendship.Status.PENDING)

    def _set_status(self, request, queryset, status):
        # .update() bypasses auto_now, so updated_at is bumped explicitly.
        updated = queryset.exclude(status=status).update(status=status, updated_at=timezone.now())
        self.message_user(
            request,
            f"{updated} friendship(s) set to {Friendship.Status(status).label.lower()}.",
            messages.SUCCESS,
        )
