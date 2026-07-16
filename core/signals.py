# your_app/signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from .models import BotConfig, Decision, Behavior, AuthorityRule, Conversation
from .governance import (
    invalidate_config_cache,
    invalidate_authority_cache,
    invalidate_behavior_cache,
    invalidate_cache,
)

@receiver([post_save, post_delete], sender=BotConfig)
def clear_config_cache(sender, instance, **kwargs):
    invalidate_config_cache()

@receiver([post_save, post_delete], sender=AuthorityRule)
def clear_authority_cache(sender, instance, **kwargs):
    invalidate_authority_cache()
    invalidate_cache("vs:*authority*")
    invalidate_cache("full_kb:*")

@receiver([post_save, post_delete], sender=Behavior)
def clear_behavior_cache(sender, instance, **kwargs):
    invalidate_behavior_cache()

@receiver([post_save, post_delete], sender=Decision)
def clear_decision_cache(sender, instance, **kwargs):
    invalidate_cache("vs:*decision*")
    invalidate_cache("full_kb:*")

@receiver([post_save], sender=Conversation)
def clear_history_cache(sender, instance, **kwargs):
    invalidate_cache(f"hist:{instance.session_id}*")
    invalidate_cache(f"full_kb:*{instance.session_id}*")