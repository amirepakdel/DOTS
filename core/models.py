from django.db import models

class Conversation(models.Model):
    session_id = models.CharField(max_length=100, db_index=True)
    role = models.CharField(max_length=20)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['session_id', 'created_at'])]

class BotConfig(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

class Decision(models.Model):
    CATEGORY_CHOICES = [
        ('pricing', 'Pricing'), ('acquisition', 'Acquisition'),
        ('negotiation', 'Negotiation'), ('risk', 'Risk'),
        ('strategy', 'Strategy'), ('legal', 'Legal'),
    ]
    AUTHORITY_CHOICES = [
        ('low', 'Low'), ('medium', 'Medium'),
        ('high', 'High'), ('forbidden', 'Forbidden'),
    ]
    ACTION_CHOICES = [
        ('buy', 'Buy'), ('reject', 'Reject'), ('negotiate', 'Negotiate'),
        ('escalate', 'Escalate'), ('delay', 'Delay'), ('conditional', 'Conditional'),
    ]

    question = models.TextField()
    context = models.TextField()
    ideal_answer = models.TextField()
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    authority_level = models.CharField(max_length=20, choices=AUTHORITY_CHOICES)
    action_type = models.CharField(max_length=50, choices=ACTION_CHOICES)
    reasoning = models.TextField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class Behavior(models.Model):
    situation = models.TextField()
    tone = models.CharField(max_length=100)
    example_response = models.TextField()
    do_rules = models.TextField()
    dont_rules = models.TextField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AuthorityRule(models.Model):
    ALLOWED_CHOICES = [
        ('yes', 'Yes'), ('no', 'No'), ('conditional', 'Conditional'),
    ]

    action_type = models.CharField(max_length=255)
    allowed = models.CharField(max_length=50, choices=ALLOWED_CHOICES)
    condition = models.TextField()
    fallback_behavior = models.TextField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

class FlaggedQuestion(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'), ('resolved', 'Resolved'), ('dismissed', 'Dismissed'),
    ]

    session_id = models.CharField(max_length=100)
    question = models.TextField()
    ai_response = models.TextField(blank=True, null=True)
    context = models.TextField(blank=True, null=True)
    flag_reason = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    admin_answer = models.TextField(blank=True, null=True)
    converted_to = models.CharField(max_length=50, blank=True, null=True)
    converted_id = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(blank=True, null=True)