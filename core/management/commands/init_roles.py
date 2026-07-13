from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType


class Command(BaseCommand):
    help = "Create default DTOS roles and assign permissions"

    ROLES = {
        "Admin": {
            "description": "Full system access",
            "permissions": "__all__",
        },
        "Governance Admin": {
            "description": "Manage decisions, behaviors, authority rules, config",
            "permissions": [
                "add_decision", "change_decision", "delete_decision", "view_decision",
                "add_behavior", "change_behavior", "delete_behavior", "view_behavior",
                "add_authorityrule", "change_authorityrule", "delete_authorityrule", "view_authorityrule",
                "add_botconfig", "change_botconfig", "view_botconfig",
            ],
        },
        "Flag Reviewer": {
            "description": "Review and resolve flagged questions",
            "permissions": [
                "view_flaggedquestion", "change_flaggedquestion",
                "add_decision", "add_behavior", "add_authorityrule",
            ],
        },
        "Moderator": {
            "description": "View governance data + review flags",
            "permissions": [
                "view_flaggedquestion", "change_flaggedquestion",
                "view_decision", "view_behavior", "view_authorityrule",
                "view_conversation",
            ],
        },
        "Chat Operator": {
            "description": "Use chat, voice, history endpoints",
            "permissions": [
                "view_botconfig", "add_conversation", "view_conversation",
            ],
        },
        "Auditor": {
            "description": "Read-only access to everything",
            "permissions": [
                "view_decision", "view_behavior", "view_authorityrule",
                "view_flaggedquestion", "view_conversation", "view_botconfig",
            ],
        },
    }

    def handle(self, *args, **options):
        for name, cfg in self.ROLES.items():
            group, created = Group.objects.get_or_create(name=name)
            action = "Created" if created else "Updated"
            
            if cfg["permissions"] == "__all__":
                perms = Permission.objects.all()
            else:
                perms = Permission.objects.filter(codename__in=cfg["permissions"])
            
            group.permissions.set(perms)
            self.stdout.write(self.style.SUCCESS(f"{action} group '{name}' with {perms.count()} permissions"))
        
        self.stdout.write(self.style.SUCCESS("All roles initialized."))