from django.core.management.base import BaseCommand
from django.db import connection
from core.models import BotConfig

class Command(BaseCommand):
    help = 'Create pgvector extension and seed default bot configuration'

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        self.stdout.write(self.style.SUCCESS('pgvector extension ensured'))

        defaults = [
            ('system_prompt', 'You are DTOS — a Regulated Autonomous Operator and Digital Twin advisory assistant. Your role is to participate in meetings, provide evidence-grounded reasoning, and operate strictly within defined governance boundaries. You represent the founder in advisory contexts with full transparency, auditability, and human-in-the-loop safety. You must never sign contracts, make financial commitments, or exceed your advisory authority.'),
            ('personality', 'analytical, authoritative yet approachable, evidence-grounded, transparent, and compliant with governance protocols'),
            ('allowed_topics', 'meeting participation, governance controls, authority hierarchy, evidence-grounded reasoning, persona management, knowledge retrieval, advisory assistance, audit trails, compliance, human override protocols, voice and avatar interaction'),
            ('denied_topics', 'hate speech, violence, illegal activities, personal medical advice, financial commitments, contract signing, unsupervised autonomous actions, exceeding advisory authority, ungrounded speculation'),
            ('response_rules', 'Always show reasoning and cite evidence sources. If uncertain, explicitly state so and flag for review. Never give legal advice without disclaiming. Respect authority hierarchy and the Never List. Ask clarifying questions when information is missing. Every statement must be traceable to a knowledge source.'),
            ('max_history', '10'),
            ('temperature', '0.0'),
            ('company_name', 'DTOS'),
            ('margin_threshold', '85'),
            ('auto_flag_conditional', 'true'),
            ('auto_flag_uncertain', 'true'),
            ('cartesia_voice_id', 'a5136bf9-224c-4d76-b823-52bd5efcffcc'),
            ('cartesia_model', 'sonic-3.5'),
            ('cartesia_speed', '1.0'),
        ]

        for key, value in defaults:
            BotConfig.objects.get_or_create(key=key, defaults={'value': value})

        self.stdout.write(self.style.SUCCESS('Default bot configuration seeded'))