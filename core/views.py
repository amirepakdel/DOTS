import requests
import logging
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django.views.generic import TemplateView
from .models import *
from .serializers import *
from .governance import *
import os
import tempfile

from io import BytesIO
from django.conf import settings

logger = logging.getLogger(__name__)

class IndexView(TemplateView):
    template_name = 'index.html'

class HealthView(APIView):
    def get(self, request):
        return Response({
            "status": "ok",
            "pending_flags": get_pending_count()
        })

class ConfigView(APIView):
    def get(self, request):
        return Response(get_config())

    def post(self, request):
        for key, value in request.data.items():
            BotConfig.objects.update_or_create(
                key=key,
                defaults={'value': str(value)}
            )
        return Response({"status": "updated", "config": get_config()})

class DecisionViewSet(viewsets.ModelViewSet):
    queryset = Decision.objects.all()
    serializer_class = DecisionSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        active_only = self.request.query_params.get('active_only', 'true').lower() == 'true'
        category = self.request.query_params.get('category')
        if active_only:
            qs = qs.filter(active=True)
        if category:
            qs = qs.filter(category=category)
        return qs.order_by('-created_at')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        try:
            add_decision_to_vectorstore(serializer.instance)
        except Exception as e:
            logger.error(f"Vector store sync failed for decision: {e}")
        return Response({"status": "added", "id": serializer.instance.id})

    @action(detail=True, methods=['post'])
    def toggle(self, request, pk=None):
        decision = self.get_object()
        decision.active = not decision.active
        decision.save()
        return Response({"status": "toggled", "active": decision.active})

    def destroy(self, request, *args, **kwargs):
        super().destroy(request, *args, **kwargs)
        return Response({"status": "deleted"})

class BehaviorViewSet(viewsets.ModelViewSet):
    queryset = Behavior.objects.all()
    serializer_class = BehaviorSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        active_only = self.request.query_params.get('active_only', 'true').lower() == 'true'
        if active_only:
            qs = qs.filter(active=True)
        return qs.order_by('-created_at')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        try:
            add_behavior_to_vectorstore(serializer.instance)
        except Exception as e:
            logger.error(f"Vector store sync failed for behavior: {e}")
        return Response({"status": "added", "id": serializer.instance.id})

    @action(detail=True, methods=['post'])
    def toggle(self, request, pk=None):
        behavior = self.get_object()
        behavior.active = not behavior.active
        behavior.save()
        return Response({"status": "toggled", "active": behavior.active})

    def destroy(self, request, *args, **kwargs):
        super().destroy(request, *args, **kwargs)
        return Response({"status": "deleted"})

class AuthorityViewSet(viewsets.ModelViewSet):
    queryset = AuthorityRule.objects.all()
    serializer_class = AuthorityRuleSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        active_only = self.request.query_params.get('active_only', 'true').lower() == 'true'
        if active_only:
            qs = qs.filter(active=True)
        return qs.order_by('-created_at')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response({"status": "added", "id": serializer.instance.id})

    @action(detail=True, methods=['post'])
    def toggle(self, request, pk=None):
        rule = self.get_object()
        rule.active = not rule.active
        rule.save()
        return Response({"status": "toggled", "active": rule.active})

    def destroy(self, request, *args, **kwargs):
        super().destroy(request, *args, **kwargs)
        return Response({"status": "deleted"})

class FlagViewSet(viewsets.ModelViewSet):
    queryset = FlaggedQuestion.objects.all()
    serializer_class = FlaggedQuestionSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs.order_by('-created_at')

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response.data = {
            "flags": response.data,
            "pending_count": get_pending_count()
        }
        return response

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response({
            "status": "flagged",
            "id": serializer.instance.id,
            "pending_count": get_pending_count()
        })

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        flag = self.get_object()
        admin_answer = request.data.get("admin_answer", "").strip()
        if not admin_answer:
            return Response({"error": "Admin answer is required"}, status=400)

        converted_to = request.data.get("converted_to")
        converted_id = None

        if converted_to == "decision":
            d = Decision.objects.create(
                question=request.data.get("question", "Flagged question"),
                context=request.data.get("context", "From review panel"),
                ideal_answer=admin_answer,
                category=request.data.get("category", "general"),
                authority_level=request.data.get("authority_level", "medium"),
                action_type=request.data.get("action_type", "escalate"),
                reasoning=request.data.get("reasoning", "Admin-provided answer from review panel")
            )
            converted_id = d.id
            try:
                add_decision_to_vectorstore(d)
            except Exception as e:
                logger.error(f"Vector store sync failed for converted decision: {e}")
        elif converted_to == "behavior":
            b = Behavior.objects.create(
                situation=request.data.get("question", "Flagged situation"),
                tone=request.data.get("tone", "professional"),
                example_response=admin_answer,
                do_rules=request.data.get("do_rules", "follow admin guidance"),
                dont_rules=request.data.get("dont_rules", "ignore admin guidance")
            )
            converted_id = b.id
            try:
                add_behavior_to_vectorstore(b)
            except Exception as e:
                logger.error(f"Vector store sync failed for converted behavior: {e}")
        elif converted_to == "authority":
            a = AuthorityRule.objects.create(
                action_type=request.data.get("action_type", "flagged action"),
                allowed=request.data.get("allowed", "conditional"),
                condition=request.data.get("condition", "reviewed by admin"),
                fallback_behavior=admin_answer
            )
            converted_id = a.id

        flag.status = 'resolved'
        flag.admin_answer = admin_answer
        flag.converted_to = converted_to
        flag.converted_id = converted_id
        flag.resolved_at = timezone.now()
        flag.save()

        return Response({
            "status": "resolved",
            "converted_to": converted_to,
            "converted_id": converted_id,
            "pending_count": get_pending_count()
        })

    @action(detail=True, methods=['post'])
    def dismiss(self, request, pk=None):
        flag = self.get_object()
        flag.status = 'dismissed'
        flag.resolved_at = timezone.now()
        flag.save()
        return Response({
            "status": "dismissed",
            "pending_count": get_pending_count()
        })

class ChatView(APIView):
    def post(self, request):
        user_message = request.data.get("message", "").strip()
        session_id = request.data.get("session_id", "default")
        use_kb = request.data.get("use_kb", True)

        if not user_message:
            return Response({"error": "Empty message"}, status=400)

        config = get_config()
        situations = detect_situation(user_message)
        violations = check_authority(user_message)
        has_forbidden = any(v['allowed'] == 'no' for v in violations)
        has_conditional = any(v['allowed'] == 'conditional' for v in violations)

        save_message(session_id, "user", user_message)

        decisions = []
        behaviors = []
        if use_kb:
            decisions = get_relevant_decisions(user_message, top_k=3)
            behaviors = get_relevant_behaviors(situations)

        history = get_history(session_id, limit=int(config.get('max_history', 10)))
        full_prompt = build_master_prompt(user_message, config, history, situations, violations, decisions, behaviors)

        reply = ""
        model_used = "unknown"

        try:
            if settings.ANTHROPIC_API_KEY:
                try:
                    response = llm_anthropic.invoke(full_prompt)
                    reply = getattr(response, 'content', None)
                    model_used = "claude-3-5-sonnet"
                except Exception as e:
                    logger.error(f"Anthropic error: {type(e).__name__}: {e}")
                    response = llm_openai.invoke(full_prompt)
                    reply = getattr(response, 'content', None)
                    model_used = "gpt-4o-mini (fallback)"
            else:
                response = llm_openai.invoke(full_prompt)
                reply = getattr(response, 'content', None)
                model_used = "gpt-4o-mini"
        except Exception as e:
            error_msg = f"LLM invocation failed: {type(e).__name__}: {str(e)}"
            logger.error(error_msg)
            reply = error_msg
            model_used = "error"

        if isinstance(reply, list):
            text_parts = []
            for block in reply:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            reply = "\n".join(text_parts).strip()
        elif reply is None:
            reply = ""
        elif not isinstance(reply, str):
            try:
                reply = str(reply)
            except Exception:
                reply = "[Error: Could not convert LLM response to string]"

        save_message(session_id, "assistant", reply)

        suggest_flag = False
        flag_reason = None
        if has_conditional and config.get('auto_flag_conditional', 'true').lower() == 'true':
            suggest_flag = True
            flag_reason = "conditional_authority"
        elif "i don't know" in reply.lower() or "uncertain" in reply.lower() or "need more information" in reply.lower():
            if config.get('auto_flag_uncertain', 'true').lower() == 'true':
                suggest_flag = True
                flag_reason = "uncertain_answer"

        return Response({
            "reply": reply,
            "session_id": session_id,
            "model": model_used,
            "situations_detected": situations,
            "authority_violations": len(violations),
            "authority_details": violations,
            "decisions_retrieved": len(decisions),
            "behaviors_applied": len(behaviors),
            "has_forbidden": has_forbidden,
            "has_conditional": has_conditional,
            "suggest_flag": suggest_flag,
            "flag_reason": flag_reason
        })

class HistoryView(APIView):
    def get(self, request):
        session_id = request.query_params.get("session_id", "default")
        qs = Conversation.objects.filter(session_id=session_id).order_by('-created_at')[:50]
        history = [{'role': c.role, 'content': c.content} for c in reversed(list(qs))]
        return Response({"history": history})

class ClearView(APIView):
    def post(self, request):
        session_id = request.data.get("session_id", "default")
        Conversation.objects.filter(session_id=session_id).delete()
        return Response({"status": "cleared"})

class StatsView(APIView):
    def get(self, request):
        stats = {
            'active_decisions': Decision.objects.filter(active=True).count(),
            'active_behaviors': Behavior.objects.filter(active=True).count(),
            'active_authority': AuthorityRule.objects.filter(active=True).count(),
            'pending_flags': FlaggedQuestion.objects.filter(status='pending').count(),
            'resolved_flags': FlaggedQuestion.objects.filter(status='resolved').count(),
            'total_messages': Conversation.objects.filter(role='user').count(),
        }
        return Response(stats)

class STTView(APIView):
    def post(self, request):
        if 'audio' not in request.FILES:
            return Response({"error": "No audio file provided"}, status=400)

        audio_file = request.FILES['audio']

        # DEBUG: check what Django actually received
        print(f"[STT] Django received: name={audio_file.name}, size={audio_file.size}, type={audio_file.content_type}")

        if audio_file.size == 0:
            return Response({"error": "Empty audio file received from browser"}, status=400)

        if not settings.CARTESIA_API_KEY:
            return Response({"error": "Cartesia API key not configured"}, status=500)

        # Write uploaded file to a real temp file on disk.
        # This is the most reliable way to forward it to Cartesia.
        suffix = os.path.splitext(audio_file.name)[1] or '.webm'
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                for chunk in audio_file.chunks():
                    tmp.write(chunk)
                tmp_path = tmp.name

            print(f"[STT] Temp file written: {tmp_path}, size={os.path.getsize(tmp_path)}")

            # Now open the real file in binary mode for requests
            with open(tmp_path, 'rb') as f:
                resp = requests.post(
                    "https://api.cartesia.ai/stt",
                    headers={
                        "Authorization": f"Bearer {settings.CARTESIA_API_KEY}",
                        "Cartesia-Version": "2026-03-01",
                    },
                    files={
                        "file": (
                            audio_file.name,
                            f,
                            audio_file.content_type or "audio/webm",
                        )
                    },
                    data={
                        "model": "ink-whisper",
                        "language": "en",
                    },
                    timeout=120,
                )

            print(f"[STT] Cartesia response: {resp.status_code} {resp.text[:200]}")

            if resp.status_code != 200:
                return Response(
                    {"error": f"Cartesia STT error: {resp.status_code} - {resp.text}"},
                    status=500,
                )

            data = resp.json()
            return Response({"transcript": data.get("text", "")})

        except Exception as e:
            print(f"[STT] Exception: {e}")
            return Response({"error": str(e)}, status=500)

        finally:
            # Always clean up the temp file
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

class TTSView(APIView):
    def post(self, request):
        text = request.data.get("text", "").strip()
        if not text:
            return Response({"error": "Text is required"}, status=400)

        config = get_config()
        voice_id = config.get("cartesia_voice_id", "a5136bf9-224c-4d76-b823-52bd5efcffcc")
        model_id = config.get("cartesia_model", "sonic-3.5")
        speed = float(config.get("cartesia_speed", "1.0"))

        if not settings.CARTESIA_API_KEY:
            return Response({"error": "Cartesia API key not configured"}, status=500)

        try:
            resp = requests.post(
                "https://api.cartesia.ai/tts/bytes",
                headers={
                    "Authorization": f"Bearer {settings.CARTESIA_API_KEY}",
                    "Content-Type": "application/json",
                    "Cartesia-Version": "2024-06-05"
                },
                json={
                    "model_id": model_id,
                    "transcript": text,
                    "voice": {"mode": "id", "id": voice_id},
                    "output_format": {"container": "mp3", "sample_rate": 24000, "encoding": "mp3"},
                    "language": "en",
                    "speed": speed
                },
                timeout=30
            )
            if resp.status_code != 200:
                return Response({"error": f"Cartesia TTS error: {resp.status_code} - {resp.text}"}, status=500)
            return HttpResponse(resp.content, content_type="audio/mpeg")
        except Exception as e:
            return Response({"error": str(e)}, status=500)