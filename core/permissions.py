from rest_framework import permissions
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType

from .models import Decision, Behavior, AuthorityRule, FlaggedQuestion, Conversation, BotConfig


class IsAdmin(permissions.BasePermission):
    """Full superuser or staff access."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class IsGovernanceAdmin(permissions.BasePermission):
    """Can manage decisions, behaviors, authority rules."""
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.groups.filter(name__in=["Governance Admin", "Admin"]).exists() or request.user.is_staff


class IsFlagReviewer(permissions.BasePermission):
    """Can review and resolve flagged questions."""
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.groups.filter(name__in=["Flag Reviewer", "Moderator", "Admin"]).exists() or request.user.is_staff

class IsModerator(permissions.BasePermission):
    """Can view governance data and review flags."""
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.groups.filter(name__in=["Moderator", "Admin"]).exists() or request.user.is_staff

class IsChatOperator(permissions.BasePermission):
    """Can use chat, history, STT, TTS endpoints."""
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.groups.filter(
            name__in=["Chat Operator", "Operator", "Admin", "Moderator"]
        ).exists() or request.user.is_staff


class IsAuditor(permissions.BasePermission):
    """Read-only access to most resources."""
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return request.user.groups.filter(name__in=["Auditor", "Viewer"]).exists() or request.user.is_staff
        return False


class ReadOnly(permissions.BasePermission):
    """Anonymous-safe read-only."""
    def has_permission(self, request, view):
        return request.method in permissions.SAFE_METHODS


# ── Helpers to bootstrap groups ──

def get_governance_permissions():
    """Return all custom permissions for this app."""
    models = [BotConfig, Decision, Behavior, AuthorityRule, FlaggedQuestion, Conversation]
    perms = []
    for model in models:
        ct = ContentType.objects.get_for_model(model)
        perms.extend(Permission.objects.filter(content_type=ct))
    return perms


DEFAULT_GROUPS = {
    "Admin": {
        "description": "Full access to all governance and chat features.",
        "permissions": "__all__",
    },
    "Governance Admin": {
        "description": "Manage decisions, behaviors, authority rules and config.",
        "permissions": [
            "add_decision", "change_decision", "delete_decision", "view_decision",
            "add_behavior", "change_behavior", "delete_behavior", "view_behavior",
            "add_authorityrule", "change_authorityrule", "delete_authorityrule", "view_authorityrule",
            "add_botconfig", "change_botconfig", "view_botconfig",
        ],
    },
    "Flag Reviewer": {
        "description": "Review flagged questions and provide admin answers.",
        "permissions": [
            "view_flaggedquestion", "change_flaggedquestion",
            "add_decision", "add_behavior", "add_authorityrule",
        ],
    },
    "Moderator": {
        "description": "Review flags + view governance data.",
        "permissions": [
            "view_flaggedquestion", "change_flaggedquestion",
            "view_decision", "view_behavior", "view_authorityrule",
            "view_conversation",
        ],
    },
    "Chat Operator": {
        "description": "Use chat, voice, and history endpoints.",
        "permissions": [
            "view_botconfig", "add_conversation", "view_conversation",
        ],
    },
    "Auditor": {
        "description": "Read-only access to decisions, behaviors, flags, and conversations.",
        "permissions": [
            "view_decision", "view_behavior", "view_authorityrule",
            "view_flaggedquestion", "view_conversation", "view_botconfig",
        ],
    },
}


def create_default_groups(apps=None, schema_editor=None, verbosity=1):
    """Idempotent helper to create default groups with permissions."""
    from django.contrib.auth.models import Group, Permission
    GroupModel = apps.get_model("auth", "Group") if apps else Group
    PermissionModel = apps.get_model("auth", "Permission") if apps else Permission

    for name, cfg in DEFAULT_GROUPS.items():
        group, created = GroupModel.objects.get_or_create(name=name)
        if verbosity:
            print(f"{'Created' if created else 'Ensured'} group: {name}")

        if cfg["permissions"] == "__all__":
            perms = PermissionModel.objects.all()
        else:
            perms = PermissionModel.objects.filter(codename__in=cfg["permissions"])
        
        group.permissions.set(perms)
        if verbosity:
            print(f"  → {perms.count()} permissions assigned")