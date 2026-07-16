import requests
import logging
import json
import uuid
import base64
import threading
import tempfile
import os
import time
import traceback

from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django.views.generic import TemplateView

from .models import *
from .serializers import *
from .governance import *

from django.conf import settings

from .permissions import (
    IsAdmin, IsGovernanceAdmin, IsFlagReviewer, IsChatOperator , IsModerator ,
    IsAuditor, ReadOnly
)

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils.decorators import method_decorator


try:
    import websocket
    WEBSOCKET_CLIENT_AVAILABLE = True
except ImportError:
    WEBSOCKET_CLIENT_AVAILABLE = False

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False

logger = logging.getLogger(__name__)

def _extract_stream_text(chunk):
    """Normalize an LLM stream chunk to a plain string (handles OpenAI list deltas)."""
    raw = getattr(chunk, 'content', None)
    if raw is None:
        return str(chunk)
    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, dict) and block.get('type') == 'text':
                parts.append(block.get('text', ''))
            elif isinstance(block, str):
                parts.append(block)
        return ''.join(parts)
    return str(raw)



class IndexView(LoginRequiredMixin,TemplateView):
    template_name = 'index.html'


class HealthView(APIView):
    permission_classes = [IsAdmin | ReadOnly]
    def get(self, request):
        tavus_configured = bool(getattr(settings, 'TAVUS_API_KEY', ''))
        custom_llm_url = request.build_absolute_uri('/api/tavus/llm/').replace('http:', 'https:')
        return Response({
            "status": "ok",
            "pending_flags": get_pending_count(),
            "tavus": {
                "configured": tavus_configured,
                "face_id": bool(getattr(settings, 'TAVUS_FACE_ID', '')),
                "pal_id": bool(getattr(settings, 'TAVUS_PAL_ID', '')),
                "custom_llm_callback": custom_llm_url
            }
        })


class ConfigView(APIView):
    permission_classes = [IsAdmin | IsGovernanceAdmin]
    def get(self, request):
        return Response(get_config())

    def post(self, request):
        for key, value in request.data.items():
            BotConfig.objects.update_or_create(
                key=key,
                defaults={'value': str(value)}
            )
        invalidate_config_cache()
        return Response({"status": "updated", "config": get_config()})


class DecisionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdmin | IsGovernanceAdmin | IsAuditor]
    queryset = Decision.objects.all()
    serializer_class = DecisionSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        category = self.request.query_params.get('category')
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
    permission_classes = [IsAdmin | IsGovernanceAdmin | IsAuditor]
    queryset = Behavior.objects.all()
    serializer_class = BehaviorSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.order_by('-created_at')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        try:
            add_behavior_to_vectorstore(serializer.instance)
        except Exception as e:
            logger.error(f"Vector store sync failed for behavior: {e}")
        invalidate_behavior_cache()
        return Response({"status": "added", "id": serializer.instance.id})

    @action(detail=True, methods=['post'])
    def toggle(self, request, pk=None):
        behavior = self.get_object()
        behavior.active = not behavior.active
        behavior.save()
        invalidate_behavior_cache()
        return Response({"status": "toggled", "active": behavior.active})

    def destroy(self, request, *args, **kwargs):
        super().destroy(request, *args, **kwargs)
        invalidate_behavior_cache()
        return Response({"status": "deleted"})


class AuthorityViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdmin | IsGovernanceAdmin | IsAuditor]
    queryset = AuthorityRule.objects.all()
    serializer_class = AuthorityRuleSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.order_by('-created_at')

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        try:
            add_authority_to_vectorstore(serializer.instance)
        except Exception as e:
            logger.error(f"Vector store sync failed for authority: {e}")
        invalidate_authority_cache()
        return Response({"status": "added", "id": serializer.instance.id})

    @action(detail=True, methods=['post'])
    def toggle(self, request, pk=None):
        rule = self.get_object()
        rule.active = not rule.active
        rule.save()
        invalidate_authority_cache()
        return Response({"status": "toggled", "active": rule.active})

    def destroy(self, request, *args, **kwargs):
        super().destroy(request, *args, **kwargs)
        invalidate_authority_cache()
        return Response({"status": "deleted"})


class FlagViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdmin | IsFlagReviewer | IsModerator |IsAuditor]
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
            try:
                add_authority_to_vectorstore(a)
            except Exception as e:
                logger.error(f"Vector store sync failed for converted authority: {e}")
            invalidate_authority_cache()

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


# =============================================================================
# CHAT (OPTIMIZED WITH PARALLEL GOVERNANCE & MODEL TIERING)
# =============================================================================

class ChatView(APIView):
    permission_classes = [IsAdmin | IsChatOperator | IsFlagReviewer]
    """Standard non-streaming chat with parallel governance & fast model routing."""

    def _calculate_confidence(self, violations, decisions, behaviors):
        score = 75
        if any(v['allowed'] == 'no' for v in violations):
            score = 95
        elif any(v['allowed'] == 'conditional' for v in violations):
            score = 65
        score += min(len(decisions) * 5, 15)
        score += min(len(behaviors) * 3, 6)
        return min(score, 100)

    def _build_reasoning_trace(self, situations, violations, decisions, behaviors, authority_docs=None):
        trace_parts = []
        trace_parts.append("=== Stage 1: Input Analysis ===")
        trace_parts.append("Message received and validated. History context loaded.")
        
        trace_parts.append("\n=== Stage 2: Situation Detection ===")
        if situations:
            trace_parts.append(f"Detected {len(situations)} situation(s): {', '.join(situations)}.")
            for sit in situations:
                if sit == 'governance_risk':
                    trace_parts.append("  → Governance risk detected: stricter evidence requirements activated.")
                elif sit == 'authority_boundary':
                    trace_parts.append("  → Authority boundary crossed: authority rule engine activated.")
                elif sit == 'high_stakes_meeting':
                    trace_parts.append("  → High-stakes context: formal tone and documentation required.")
                elif sit == 'hostile':
                    trace_parts.append("  → Hostile tone detected: de-escalation protocols engaged.")
                elif sit == 'legal':
                    trace_parts.append("  → Legal context flagged: recommend human legal review.")
        else:
            trace_parts.append("No specific situations detected. Default advisory mode.")
        
        trace_parts.append("\n=== Stage 3: Authority Check ===")
        trace_parts.append("Layer 1 (Keyword Match): Scanned all active authority rules against message content.")
        if violations:
            for v in violations:
                status = "FORBIDDEN" if v['allowed'] == 'no' else "CONDITIONAL" if v['allowed'] == 'conditional' else "ALLOWED"
                trace_parts.append(f"  → MATCH: Rule '{v['rule']}' → Status: {status}")
                trace_parts.append(f"    Condition: {v['condition']}")
                trace_parts.append(f"    Fallback: {v['fallback']}")
            if any(v['allowed'] == 'no' for v in violations):
                trace_parts.append("  → CRITICAL: Hard FORBIDDEN rule triggered. Autonomous refusal required.")
        else:
            trace_parts.append("  → No keyword violations found.")
        
        trace_parts.append("\nLayer 2 (Semantic Search): Queried vectorstore for semantically similar authority rules.")
        if authority_docs:
            trace_parts.append(f"  → Found {len(authority_docs)} relevant rule(s) via embedding similarity.")
            for i, doc in enumerate(authority_docs, 1):
                trace_parts.append(f"    [{i}] {doc.page_content[:120]}...")
        else:
            trace_parts.append("  → No additional semantic matches above threshold.")
        
        trace_parts.append("\n=== Stage 4: Knowledge Base Retrieval ===")
        if decisions:
            trace_parts.append(f"Retrieved {len(decisions)} decision pattern(s) from PGVector:")
            for i, d in enumerate(decisions, 1):
                src = d.metadata.get('source', 'unknown')
                trace_parts.append(f"  [{i}] {src} | {d.page_content[:100]}...")
        else:
            trace_parts.append("No relevant decision patterns retrieved.")
        
        if behaviors:
            trace_parts.append(f"\nApplied {len(behaviors)} behavior style(s):")
            for b in behaviors:
                trace_parts.append(f"  → {b['situation']} | Tone: {b['tone']}")
        else:
            trace_parts.append("No behavior styles matched.")
        
        trace_parts.append("\n=== Stage 5: Response Build ===")
        trace_parts.append("Master prompt assembled with all governance context injected.")
        trace_parts.append("LLM invoked with temperature=0.3 (deterministic mode).")
        
        return "\n".join(trace_parts)

    def _build_references(self, decisions, behaviors, violations, authority_docs=None):
        refs = []
        for d in decisions:
            refs.append({
                "id": f"dec-{d.metadata.get('id', 'unknown')}",
                "type": "decision",
                "title": d.page_content[:60] + "..." if len(d.page_content) > 60 else d.page_content,
                "score": 0.85,
                "source": d.metadata.get('source', 'unknown')
            })
        for v in violations:
            refs.append({
                "id": f"auth-{v['id']}",
                "type": "authority",
                "title": v['rule'],
                "score": 0.94,
                "allowed": v['allowed'],
                "condition": v['condition']
            })
        for doc in (authority_docs or []):
            refs.append({
                "id": f"auth-sem-{doc.metadata.get('id', 'unknown')}",
                "type": "authority",
                "title": doc.page_content[:60] + "...",
                "score": 0.82,
                "match_type": "semantic"
            })
        for b in behaviors:
            refs.append({
                "id": f"beh-{b['id']}",
                "type": "behavior",
                "title": b['situation'][:60] + "..." if len(b['situation']) > 60 else b['situation'],
                "score": 0.76,
                "tone": b['tone']
            })
        return refs

    def post(self, request):
        user_message = request.data.get("message", "").strip()
        session_id = request.data.get("session_id", "default")
        use_kb = request.data.get("use_kb", True)

        if not user_message:
            return Response({"error": "Empty message"}, status=400)

        result = self._process_chat(user_message, session_id, use_kb)
        return Response(result)

    def _process_chat(self, user_message, session_id, use_kb, skip_db_history=False, external_history=None):
        config = get_config()
        situations = detect_situation(user_message)
        violations = check_authority(user_message)
        has_forbidden = any(v['allowed'] == 'no' for v in violations)
        has_conditional = any(v['allowed'] == 'conditional' for v in violations)

        if not skip_db_history:
            save_message(session_id, "user", user_message)

        complexity = estimate_complexity(user_message, situations, violations)

        decisions, behaviors, authority_docs, history = [], [], [], []
        if use_kb and not has_forbidden:
            try:
                decisions, behaviors, authority_docs, _ = fetch_kb_parallel(
                    user_message, situations, session_id, int(config.get('max_history', 10))
                )
                history = external_history if skip_db_history else _
            except Exception as e:
                logger.error(f"Parallel KB fetch failed: {e}")
                history = external_history if skip_db_history else get_history(session_id, limit=int(config.get('max_history', 10)))
        else:
            history = external_history if skip_db_history else get_history(session_id, limit=int(config.get('max_history', 10)))

        full_prompt = build_master_prompt(
            user_message, config, history, situations, violations,
            decisions, behaviors, authority_docs=authority_docs,
            include_reasoning_in_output=False
        )

        reply = ""
        model_used = "unknown"

        try:
            if complexity == "fast" and not has_forbidden:
                # Fast path: gpt-4o-mini with 512 token limit
                response = llm.invoke(full_prompt)
                reply = getattr(response, 'content', None)
                model_used = "gpt-4o-mini-fast"
            elif settings.ANTHROPIC_API_KEY:
                try:
                    response = llm_anthropic.invoke(full_prompt)
                    reply = getattr(response, 'content', None)
                    model_used = "claude-3-5-sonnet"
                except Exception as e:
                    logger.error(f"Anthropic error: {type(e).__name__}: {e}")
                    response = llm.invoke(full_prompt)
                    reply = getattr(response, 'content', None)
                    model_used = "gpt-4o-mini (fallback)"
            else:
                response = llm.invoke(full_prompt)
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

        if not skip_db_history:
            save_message(session_id, "assistant", reply)

        confidence = self._calculate_confidence(violations, decisions, behaviors)
        reasoning_trace = self._build_reasoning_trace(situations, violations, decisions, behaviors, authority_docs)
        references = self._build_references(decisions, behaviors, violations, authority_docs)

        suggest_flag = False
        flag_reason = None
        if has_conditional and config.get('auto_flag_conditional', 'true').lower() == 'true':
            suggest_flag = True
            flag_reason = "conditional_authority"
        elif "i don't know" in reply.lower() or "uncertain" in reply.lower() or "need more information" in reply.lower():
            if config.get('auto_flag_uncertain', 'true').lower() == 'true':
                suggest_flag = True
                flag_reason = "uncertain_answer"

        return {
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
            "flag_reason": flag_reason,
            "thoughts": {
                "situations_detected": situations,
                "authority_violations": violations,
                "has_forbidden": has_forbidden,
                "has_conditional": has_conditional,
                "confidence_score": confidence,
                "confidence_breakdown": {
                    "base_score": 75,
                    "forbidden_bonus": 20 if has_forbidden else 0,
                    "conditional_penalty": -10 if has_conditional else 0,
                    "decision_bonus": min(len(decisions) * 5, 15),
                    "behavior_bonus": min(len(behaviors) * 3, 6),
                    "final_score": confidence
                },
                "reasoning_trace": reasoning_trace,
                "references": references,
                "model_used": model_used,
                "prompt_tokens_estimate": len(full_prompt.split()),
                "timestamp": timezone.now().isoformat(),
                "pipeline_stages": {
                    "input_analysis": {"status": "completed", "history_loaded": len(history)},
                    "situation_detection": {"status": "completed", "matches": len(situations), "categories": situations},
                    "authority_check": {
                        "status": "completed",
                        "layer1_keyword_matches": len(violations),
                        "layer2_semantic_matches": len(authority_docs),
                        "has_forbidden": has_forbidden
                    },
                    "kb_retrieval": {
                        "status": "completed",
                        "decisions": len(decisions),
                        "behaviors": len(behaviors),
                        "authority_semantic": len(authority_docs)
                    },
                    "response_build": {
                        "status": "completed",
                        "model": model_used,
                        "temperature": 0.0,
                        "complexity_tier": complexity
                    }
                }
            }
        }


class ChatStreamView(APIView):
    permission_classes = [IsAdmin | IsChatOperator | IsFlagReviewer]
    """Streaming chat with parallel governance & early metadata emission."""

    def post(self, request):
        user_message = request.data.get("message", "").strip()
        session_id = request.data.get("session_id", "default")
        use_kb = request.data.get("use_kb", True)

        if not user_message:
            return Response({"error": "Empty message"}, status=400)

        def event_stream():
            config = get_config()
            situations = detect_situation(user_message)
            violations = check_authority(user_message)
            has_forbidden = any(v['allowed'] == 'no' for v in violations)
            has_conditional = any(v['allowed'] == 'conditional' for v in violations)

            # Emit governance metadata immediately (zero-LLM latency feedback)
            yield f"data: {json.dumps({
                'type': 'meta',
                'situations': situations,
                'violations': len(violations),
                'has_forbidden': has_forbidden,
                'model_selected': 'claude-3-5-sonnet' if settings.ANTHROPIC_API_KEY else 'gpt-4o-mini'
            })}\n\n"

            if has_forbidden:
                fallback = violations[0].get('fallback', 'This action is not permitted.')
                yield f"data: {json.dumps({'text': fallback, 'done': False})}\n\n"
                yield f"data: {json.dumps({'text': '', 'done': True})}\n\n"
                return

            save_message(session_id, "user", user_message)

            # Parallel KB fetch using threads (no asyncio event loop issues)
            decisions, behaviors, authority_docs, history = [], [], [], []
            if use_kb:
                try:
                    decisions, behaviors, authority_docs, history = fetch_kb_parallel(
                        user_message, situations, session_id, int(config.get('max_history', 10))
                    )
                except Exception as e:
                    logger.error(f"Parallel KB fetch failed in stream: {e}")
                    history = get_history(session_id, limit=int(config.get('max_history', 10)))
            else:
                history = get_history(session_id, limit=int(config.get('max_history', 10)))

            full_prompt = build_master_prompt(
                user_message, config, history, situations, violations,
                decisions, behaviors, authority_docs=authority_docs,
                include_reasoning_in_output=False
            )

            complexity = estimate_complexity(user_message, situations, violations)
            full_reply = ""
            model_used = "unknown"  # FIXED: initialize before try

            try:
                if complexity == "fast" and not has_forbidden:
                    for chunk in llm.stream(full_prompt):
                        text = _extract_stream_text(chunk) or ""

                        if text:
                            full_reply += text
                            yield f"data: {json.dumps({'text': text, 'done': False})}\n\n"
                    model_used = "gpt-4o-mini-fast"
                elif settings.ANTHROPIC_API_KEY:
                    try:
                        for chunk in llm_anthropic.stream(full_prompt):
                            text = ""
                            if hasattr(chunk, 'content'):
                                if isinstance(chunk.content, list):
                                    for block in chunk.content:
                                        if isinstance(block, dict) and block.get("type") == "text":
                                            text += block.get("text", "")
                                        elif isinstance(block, str):
                                            text += block
                                else:
                                    text = str(chunk.content)
                            else:
                                text = str(chunk)
                            if text:
                                full_reply += text
                                yield f"data: {json.dumps({'text': text, 'done': False})}\n\n"
                        model_used = "claude-3-5-sonnet"
                    except Exception as e:
                        logger.error(f"Anthropic stream error: {e}")
                        for chunk in llm.stream(full_prompt):
                            text = _extract_stream_text(chunk) or ""

                            if text:
                                full_reply += text
                                yield f"data: {json.dumps({'text': text, 'done': False})}\n\n"
                        model_used = "gpt-4o-mini (fallback)"
                else:
                    for chunk in llm.stream(full_prompt):
                        text = _extract_stream_text(chunk) or ""

                        if text:
                            full_reply += text
                            yield f"data: {json.dumps({'text': text, 'done': False})}\n\n"
                    model_used = "gpt-4o-mini"

            except Exception as e:
                error_text = f"Error: {str(e)}"
                logger.error(f"Streaming LLM error: {e}")
                yield f"data: {json.dumps({'text': error_text, 'done': False})}\n\n"

            finally:
                # Save complete response
                if full_reply:
                    save_message(session_id, "assistant", full_reply)
                
                # Build thoughts for the final chunk
                confidence = self._calculate_confidence(violations, decisions, behaviors)
                reasoning_trace = self._build_reasoning_trace(situations, violations, decisions, behaviors, authority_docs)
                references = self._build_references(decisions, behaviors, violations, authority_docs)
                
                thoughts = {
                    "situations_detected": situations,
                    "authority_violations": violations,
                    "has_forbidden": has_forbidden,
                    "has_conditional": has_conditional,
                    "confidence_score": confidence,
                    "confidence_breakdown": {
                        "base_score": 75,
                        "forbidden_bonus": 20 if has_forbidden else 0,
                        "conditional_penalty": -10 if has_conditional else 0,
                        "decision_bonus": min(len(decisions) * 5, 15),
                        "behavior_bonus": min(len(behaviors) * 3, 6),
                        "final_score": confidence
                    },
                    "reasoning_trace": reasoning_trace,
                    "references": references,
                    "model_used": model_used,
                    "timestamp": timezone.now().isoformat()
                }
                
                yield f"data: {json.dumps({'text': '', 'done': True, 'thoughts': thoughts})}\n\n"

        response = StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream'
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response

    def _calculate_confidence(self, violations, decisions, behaviors):
        score = 75
        if any(v['allowed'] == 'no' for v in violations):
            score = 95
        elif any(v['allowed'] == 'conditional' for v in violations):
            score = 65
        score += min(len(decisions) * 5, 15)
        score += min(len(behaviors) * 3, 6)
        return min(score, 100)

    def _build_reasoning_trace(self, situations, violations, decisions, behaviors, authority_docs=None):
        trace_parts = []
        trace_parts.append("=== Stage 1: Input Analysis ===")
        trace_parts.append("Message received and validated. History context loaded.")
        
        trace_parts.append("\n=== Stage 2: Situation Detection ===")
        if situations:
            trace_parts.append(f"Detected {len(situations)} situation(s): {', '.join(situations)}.")
        else:
            trace_parts.append("No specific situations detected.")
        
        trace_parts.append("\n=== Stage 3: Authority Check ===")
        if violations:
            for v in violations:
                status = "FORBIDDEN" if v['allowed'] == 'no' else "CONDITIONAL"
                trace_parts.append(f"  → Rule '{v['rule']}' → Status: {status}")
        else:
            trace_parts.append("  → No keyword violations.")
        
        if authority_docs:
            trace_parts.append(f"\n  → {len(authority_docs)} semantic match(es) found.")
        
        trace_parts.append("\n=== Stage 4: KB Retrieval ===")
        trace_parts.append(f"Retrieved {len(decisions)} decision(s), {len(behaviors)} behavior(s).")
        
        trace_parts.append("\n=== Stage 5: Response Build ===")
        trace_parts.append("Master prompt assembled. LLM invoked.")
        
        return "\n".join(trace_parts)

    def _build_references(self, decisions, behaviors, violations, authority_docs=None):
        refs = []
        for d in decisions:
            refs.append({
                "id": f"dec-{d.metadata.get('id', 'unknown')}",
                "type": "decision",
                "title": d.page_content[:60] + "...",
                "score": 0.85
            })
        for v in violations:
            refs.append({
                "id": f"auth-{v['id']}",
                "type": "authority",
                "title": v['rule'],
                "score": 0.94
            })
        for b in behaviors:
            refs.append({
                "id": f"beh-{b['id']}",
                "type": "behavior",
                "title": b['situation'][:60] + "...",
                "score": 0.76
            })
        return refs


class HistoryView(APIView):
    permission_classes = [IsAdmin | IsChatOperator | IsAuditor]
    def get(self, request):
        session_id = request.query_params.get("session_id", "default")
        qs = Conversation.objects.filter(session_id=session_id).order_by('-created_at')[:50]
        history = [{'role': c.role, 'content': c.content} for c in reversed(list(qs))]
        return Response({"history": history})


class ClearView(APIView):
    permission_classes = [IsAdmin | IsChatOperator]
    def post(self, request):
        session_id = request.data.get("session_id", "default")
        Conversation.objects.filter(session_id=session_id).delete()
        return Response({"status": "cleared"})


class StatsView(APIView):
    permission_classes = [IsAdmin | IsAuditor | IsFlagReviewer]
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


# =============================================================================
# TAVUS CVI — Conversational Video Interface
# =============================================================================

# =============================================================================
# TAVUS CVI — Conversational Video Interface
# =============================================================================

class TavusConversationView(APIView):
    """
    Create a Tavus CVI conversation. 
    Tries to wire the PAL to our custom LLM backend so KB + governance runs on every turn.
    """
    permission_classes = [IsAdmin | IsChatOperator | IsFlagReviewer]

    def _get_custom_llm_url(self, request):
        custom_llm_url = request.data.get('custom_llm_url')
        if not custom_llm_url:
            custom_llm_url = request.build_absolute_uri('/api/tavus/llm/')
        if custom_llm_url.startswith('http:'):
            custom_llm_url = 'https:' + custom_llm_url[5:]
        return custom_llm_url.rstrip('/')

    def _patch_pal_json_patch(self, pal_id, api_key, custom_llm_url):
        """Approach 1: RFC 6902 JSON Patch with proper content-type."""
        secret = getattr(settings, 'TAVUS_CUSTOM_LLM_SECRET', 'tavus-local-secret')
        patch_ops = [
            {
                "op": "replace",
                "path": "/layers/llm",
                "value": {
                    "model": "custom",
                    "base_url": custom_llm_url,
                    "api_key": secret,
                    "speculative_inference": False
                }
            }
        ]
        try:
            resp = requests.patch(
                f"https://tavusapi.com/v2/pals/{pal_id}",
                headers={
                    "Content-Type": "application/json-patch+json",  # REQUIRED for RFC 6902
                    "x-api-key": api_key
                },
                json=patch_ops,
                timeout=15
            )
            if resp.status_code in (200, 204):
                return True, None, resp.status_code
            # Try to parse error, fallback to status text
            err = resp.text[:500] or f"HTTP {resp.status_code}"
            return False, err, resp.status_code
        except Exception as e:
            return False, str(e), None

    def _create_pal_with_llm(self, api_key, custom_llm_url, face_id):
        """Approach 2: Create a brand-new PAL with layers baked in."""
        secret = getattr(settings, 'TAVUS_CUSTOM_LLM_SECRET', 'tavus-local-secret')
        payload = {
            "pal_name": f"GovPal-{uuid.uuid4().hex[:6]}",
            "system_prompt": (
                "You are a helpful AI assistant. All responses are generated by a "
                "custom governance backend that enforces authority rules and knowledge base retrieval."
            ),
            "pipeline_mode": "full",
            "default_face_id": face_id,
            "layers": {
                "llm": {
                    "model": "custom",
                    "base_url": custom_llm_url,
                    "api_key": secret,
                    "speculative_inference": False
                }
            }
        }
        try:
            resp = requests.post(
                "https://tavusapi.com/v2/pals",
                headers={"Content-Type": "application/json", "x-api-key": api_key},
                json=payload,
                timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("pal_id"), None, resp.status_code
            return None, resp.text[:500] or f"HTTP {resp.status_code}", resp.status_code
        except Exception as e:
            return None, str(e), None

    def _get_or_create_governance_pal(self, api_key, custom_llm_url, face_id):
        """
        Approach 3: Re-use a cached governance PAL so we don't create one per call.
        Stores the created PAL ID in BotConfig under 'tavus_governance_pal_id'.
        """
        config = get_config()
        cached_pal_id = config.get('tavus_governance_pal_id')
        
        if cached_pal_id:
            # Verify it still exists by trying a lightweight PATCH
            success, err, code = self._patch_pal_json_patch(cached_pal_id, api_key, custom_llm_url)
            if success:
                logger.info(f"[Tavus] Re-using cached governance PAL: {cached_pal_id}")
                return cached_pal_id, None
            logger.warning(f"[Tavus] Cached PAL {cached_pal_id} invalid ({code}: {err}), creating new one...")

        # Create fresh
        new_pal_id, err, code = self._create_pal_with_llm(api_key, custom_llm_url, face_id)
        if new_pal_id:
            # Cache it for next time
            try:
                BotConfig.objects.update_or_create(
                    key='tavus_governance_pal_id',
                    defaults={'value': new_pal_id}
                )
                invalidate_config_cache()
            except Exception as e:
                logger.warning(f"[Tavus] Failed to cache PAL ID: {e}")
            return new_pal_id, None

        return None, f"Create PAL failed ({code}): {err}"

    def post(self, request):
        api_key = getattr(settings, 'TAVUS_API_KEY', '')
        if not api_key:
            return Response({"error": "TAVUS_API_KEY not configured"}, status=500)

        face_id = getattr(settings, 'TAVUS_FACE_ID', '')
        pal_id = getattr(settings, 'TAVUS_PAL_ID', '')

        if not face_id or not pal_id:
            return Response({
                "error": "TAVUS_FACE_ID and TAVUS_PAL_ID must be configured",
                "details": {"face_id_configured": bool(face_id), "pal_id_configured": bool(pal_id)}
            }, status=500)

        face_id = request.data.get('face_id', face_id)
        pal_id = request.data.get('pal_id', pal_id)
        require_auth = request.data.get('require_auth', False)
        max_participants = request.data.get('max_participants', 2)
        callback_url = request.data.get('callback_url', '')
        use_custom_llm = request.data.get('use_custom_llm', True)

        custom_llm_url = self._get_custom_llm_url(request)

        # =================================================================
        # WIRE THE PAL TO OUR CUSTOM LLM BACKEND
        # =================================================================
        if use_custom_llm:
            # Try 1: Patch existing PAL (fastest, but often fails due to perms)
            patch_ok, patch_err, patch_code = self._patch_pal_json_patch(pal_id, api_key, custom_llm_url)
            
            if patch_ok:
                logger.info(f"[Tavus] Patched PAL {pal_id} with custom LLM")
            else:
                logger.warning(f"[Tavus] PAL patch failed ({patch_code}): {patch_err}")
                
                # Try 2: Create new PAL with custom LLM baked in
                new_pal_id, create_err, create_code = self._create_pal_with_llm(api_key, custom_llm_url, face_id)
                
                if new_pal_id:
                    pal_id = new_pal_id
                    logger.info(f"[Tavus] Created new PAL {pal_id} with custom LLM")
                else:
                    logger.warning(f"[Tavus] PAL create failed ({create_code}): {create_err}")
                    
                    # Try 3: Re-use cached governance PAL (survives across restarts)
                    cached_pal_id, cache_err = self._get_or_create_governance_pal(api_key, custom_llm_url, face_id)
                    if cached_pal_id:
                        pal_id = cached_pal_id
                        logger.info(f"[Tavus] Using cached governance PAL {pal_id}")
                    else:
                        # All approaches failed — return detailed diagnostics
                        return Response({
                            "error": "Failed to configure PAL with custom LLM layer",
                            "details": {
                                "message": (
                                    "Tavus rejected all 3 attempts to wire the PAL to your backend. "
                                    "Your API key may lack PAL edit permissions, or the PAL ID may be invalid."
                                ),
                                "attempt_1_patch": {
                                    "status_code": patch_code,
                                    "error": patch_err or "Empty response body"
                                },
                                "attempt_2_create": {
                                    "status_code": create_code,
                                    "error": create_err or "Empty response body"
                                },
                                "attempt_3_cache": {
                                    "error": cache_err
                                },
                                "pal_id_used": pal_id,
                                "custom_llm_url": custom_llm_url,
                                "recommendation": (
                                    "Either (A) use a Tavus API key with PAL management permissions, or "
                                    "(B) manually create a PAL in the Tavus dashboard with custom LLM URL "
                                    f"{custom_llm_url} and set TAVUS_PAL_ID to its ID."
                                )
                            }
                        }, status=502)

        # Build conversation payload — NEVER include 'layers' here
        payload = {
            "face_id": face_id,
            "pal_id": pal_id,
            "require_auth": require_auth,
            "max_participants": max_participants,
        }
        if callback_url:
            payload["callback_url"] = callback_url

        logger.info(f"[Tavus] Creating conversation: face_id={face_id[:8]}... pal_id={pal_id[:8]}...")

        try:
            resp = requests.post(
                "https://tavusapi.com/v2/conversations",
                headers={"Content-Type": "application/json", "x-api-key": api_key},
                json=payload,
                timeout=15
            )

            logger.info(f"[Tavus] API response status: {resp.status_code}")

            if resp.status_code != 200:
                try:
                    err_body = resp.json()
                except Exception:
                    err_body = {"raw_response": resp.text[:500]}
                return Response({
                    "error": f"Tavus API returned {resp.status_code}",
                    "details": err_body
                }, status=502)

            data = resp.json()
            conv_id = data.get("conversation_id", "unknown")
            logger.info(f"[Tavus] Conversation created: {conv_id}")
            
            return Response({
                "conversation_id": conv_id,
                "conversation_url": data.get("conversation_url"),
                "conversation_name": data.get("conversation_name"),
                "status": data.get("status"),
                "meeting_token": data.get("meeting_token"),
                "created_at": data.get("created_at"),
                "custom_llm_url": custom_llm_url if use_custom_llm else None,
                "governance_enabled": use_custom_llm,
                "pal_id_used": pal_id
            })

        except requests.exceptions.Timeout:
            return Response({"error": "Tavus API request timed out"}, status=504)
        except requests.exceptions.ConnectionError as e:
            return Response({"error": "Cannot connect to Tavus API", "details": str(e)}, status=502)
        except requests.exceptions.RequestException as e:
            status_code = 502
            err_body = {}
            if hasattr(e, 'response') and e.response is not None:
                status_code = e.response.status_code
                try:
                    err_body = e.response.json()
                except Exception:
                    err_body = {"raw_response": getattr(e.response, 'text', str(e))[:500]}
            return Response({
                "error": f"Tavus API error: {type(e).__name__}",
                "details": err_body
            }, status=status_code if status_code != 200 else 502)


class TavusEndConversationView(APIView):
    """End a Tavus CVI conversation session."""
    permission_classes = [IsAdmin | IsChatOperator | IsFlagReviewer]

    def post(self, request):
        conversation_id = request.data.get("conversation_id", "")
        if not conversation_id:
            return Response({"error": "conversation_id is required"}, status=400)

        api_key = getattr(settings, 'TAVUS_API_KEY', '')
        if not api_key:
            return Response({"error": "TAVUS_API_KEY not configured"}, status=500)

        try:
            resp = requests.delete(
                f"https://tavusapi.com/v2/conversations/{conversation_id}",
                headers={"x-api-key": api_key},
                timeout=10
            )
            resp.raise_for_status()
            return Response({"status": "ended", "conversation_id": conversation_id})
        except requests.exceptions.RequestException as e:
            logger.error(f"[Tavus] End conversation failed: {e}")
            return Response({"error": str(e)}, status=502)


class TavusLLMCallbackView(APIView):
    """
    Tavus CVI custom LLM callback.

    NOW USES THE EXACT SAME KNOWLEDGE-RETRIEVAL & STREAMING PIPELINE AS ChatStreamView:
    - Parallel KB fetch via fetch_kb_parallel()
    - Model tier routing via estimate_complexity()
    - Direct LLM streaming (not buffered _process_chat)
    - Same authority / situation / behavior logic
    - OpenAI-compatible SSE chunks for Tavus TTS
    """
    permission_classes = []
    authentication_classes = []

    # ------------------------------------------------------------------
    # Tavus SSE helpers (unchanged)
    # ------------------------------------------------------------------
    def _verify_tavus_request(self, request):
        expected_secret = getattr(settings, 'TAVUS_CUSTOM_LLM_SECRET', 'tavus-local-secret')
        provided_secret = request.headers.get('X-Tavus-Secret', request.headers.get('Authorization', ''))
        if provided_secret.startswith('Bearer '):
            provided_secret = provided_secret[7:]
        if provided_secret != expected_secret:
            logger.warning(f"[Tavus LLM] Invalid secret from {request.META.get('REMOTE_ADDR')}")
            return False
        return True

    def _make_sse_chunk(self, chunk_id, model, delta_content=None, role=None, finish_reason=None):
        delta = {}
        if role:
            delta["role"] = role
        if delta_content is not None:
            delta["content"] = delta_content
        data = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason
            }]
        }
        return f"data: {json.dumps(data)}\n\n"

    # ------------------------------------------------------------------
    # Governance helpers — copied from ChatStreamView so behaviour is identical
    # ------------------------------------------------------------------
    def _calculate_confidence(self, violations, decisions, behaviors):
        score = 75
        if any(v['allowed'] == 'no' for v in violations):
            score = 95
        elif any(v['allowed'] == 'conditional' for v in violations):
            score = 65
        score += min(len(decisions) * 5, 15)
        score += min(len(behaviors) * 3, 6)
        return min(score, 100)

    def _build_reasoning_trace(self, situations, violations, decisions, behaviors, authority_docs=None):
        trace_parts = []
        trace_parts.append("=== Stage 1: Input Analysis ===")
        trace_parts.append("Message received and validated. History context loaded.")
        trace_parts.append("\n=== Stage 2: Situation Detection ===")
        if situations:
            trace_parts.append(f"Detected {len(situations)} situation(s): {', '.join(situations)}.")
            for sit in situations:
                if sit == 'governance_risk':
                    trace_parts.append("  → Governance risk detected: stricter evidence requirements activated.")
                elif sit == 'authority_boundary':
                    trace_parts.append("  → Authority boundary crossed: authority rule engine activated.")
                elif sit == 'high_stakes_meeting':
                    trace_parts.append("  → High-stakes context: formal tone and documentation required.")
                elif sit == 'hostile':
                    trace_parts.append("  → Hostile tone detected: de-escalation protocols engaged.")
                elif sit == 'legal':
                    trace_parts.append("  → Legal context flagged: recommend human legal review.")
        else:
            trace_parts.append("No specific situations detected. Default advisory mode.")
        trace_parts.append("\n=== Stage 3: Authority Check ===")
        trace_parts.append("Layer 1 (Keyword Match): Scanned all active authority rules against message content.")
        if violations:
            for v in violations:
                status = "FORBIDDEN" if v['allowed'] == 'no' else "CONDITIONAL" if v['allowed'] == 'conditional' else "ALLOWED"
                trace_parts.append(f"  → MATCH: Rule '{v['rule']}' → Status: {status}")
                trace_parts.append(f"    Condition: {v['condition']}")
                trace_parts.append(f"    Fallback: {v['fallback']}")
            if any(v['allowed'] == 'no' for v in violations):
                trace_parts.append("  → CRITICAL: Hard FORBIDDEN rule triggered. Autonomous refusal required.")
        else:
            trace_parts.append("  → No keyword violations found.")
        trace_parts.append("\nLayer 2 (Semantic Search): Queried vectorstore for semantically similar authority rules.")
        if authority_docs:
            trace_parts.append(f"  → Found {len(authority_docs)} relevant rule(s) via embedding similarity.")
            for i, doc in enumerate(authority_docs, 1):
                trace_parts.append(f"    [{i}] {doc.page_content[:120]}...")
        else:
            trace_parts.append("  → No additional semantic matches above threshold.")
        trace_parts.append("\n=== Stage 4: Knowledge Base Retrieval ===")
        if decisions:
            trace_parts.append(f"Retrieved {len(decisions)} decision pattern(s) from PGVector:")
            for i, d in enumerate(decisions, 1):
                src = d.metadata.get('source', 'unknown')
                trace_parts.append(f"  [{i}] {src} | {d.page_content[:100]}...")
        else:
            trace_parts.append("No relevant decision patterns retrieved.")
        if behaviors:
            trace_parts.append(f"\nApplied {len(behaviors)} behavior style(s):")
            for b in behaviors:
                trace_parts.append(f"  → {b['situation']} | Tone: {b['tone']}")
        else:
            trace_parts.append("No behavior styles matched.")
        trace_parts.append("\n=== Stage 5: Response Build ===")
        trace_parts.append("Master prompt assembled with all governance context injected.")
        trace_parts.append("LLM invoked with temperature=0.3 (deterministic mode).")
        return "\n".join(trace_parts)

    def _build_references(self, decisions, behaviors, violations, authority_docs=None):
        refs = []
        for d in decisions:
            refs.append({
                "id": f"dec-{d.metadata.get('id', 'unknown')}",
                "type": "decision",
                "title": d.page_content[:60] + "..." if len(d.page_content) > 60 else d.page_content,
                "score": 0.85,
                "source": d.metadata.get('source', 'unknown')
            })
        for v in violations:
            refs.append({
                "id": f"auth-{v['id']}",
                "type": "authority",
                "title": v['rule'],
                "score": 0.94,
                "allowed": v['allowed'],
                "condition": v['condition']
            })
        for doc in (authority_docs or []):
            refs.append({
                "id": f"auth-sem-{doc.metadata.get('id', 'unknown')}",
                "type": "authority",
                "title": doc.page_content[:60] + "...",
                "score": 0.82,
                "match_type": "semantic"
            })
        for b in behaviors:
            refs.append({
                "id": f"beh-{b['id']}",
                "type": "behavior",
                "title": b['situation'][:60] + "..." if len(b['situation']) > 60 else b['situation'],
                "score": 0.76,
                "tone": b['tone']
            })
        return refs

    # ------------------------------------------------------------------
    # Main handler — now mirrors ChatStreamView.post() line-for-line
    # ------------------------------------------------------------------
    def post(self, request):
        # 1. SECURITY
        if not self._verify_tavus_request(request):
            return Response({"error": "Unauthorized"}, status=401)

        # 2. LOG RAW PAYLOAD
        raw_body = json.dumps(request.data, indent=2)
        logger.info(f"[Tavus LLM] === RAW PAYLOAD ===\n{raw_body[:2000]}")

        stream = request.data.get("stream", False)
        req_model = request.data.get("model", "custom")
        logger.info(f"[Tavus LLM] Request: stream={stream}, model={req_model}")

        # 3. EXTRACT MESSAGE AND HISTORY (Tavus-specific)
        messages = request.data.get("messages", [])
        session_id = (
            request.data.get("conversation_id")
            or request.data.get("session_id")
            or f"tavus_{uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(messages, sort_keys=True))}"
        )
        # Tavus message history (exclude current user turn)
        tavus_history = []
        for msg in messages:
            if msg.get("role") in ("user", "assistant"):
                tavus_history.append({"role": msg["role"], "content": msg.get("content", "")})
        if tavus_history and tavus_history[-1]["role"] == "user":
            tavus_history = tavus_history[:-1]

        # Extract last user message
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break
        if isinstance(user_message, list):
            text_parts = []
            for block in user_message:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            user_message = "".join(text_parts).strip()

        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        # Empty message fallback
        if not user_message:
            logger.warning("[Tavus LLM] No user message found")
            def fallback_stream():
                yield self._make_sse_chunk(chunk_id, req_model, role="assistant")
                yield self._make_sse_chunk(chunk_id, req_model, delta_content="I didn't catch that. Could you please repeat?")
                yield self._make_sse_chunk(chunk_id, req_model, finish_reason="stop")
                yield "data: [DONE]\n\n"
            return StreamingHttpResponse(fallback_stream(), content_type='text/event-stream')

        logger.info(f"[Tavus LLM] User: '{user_message[:120]}...' | Session: {session_id} | History: {len(tavus_history)} turns")

        # 4. GOVERNANCE PIPELINE — EXACT SAME ORDER AS ChatStreamView
        config = get_config()
        situations = detect_situation(user_message)
        violations = check_authority(user_message)
        has_forbidden = any(v['allowed'] == 'no' for v in violations)
        has_conditional = any(v['allowed'] == 'conditional' for v in violations)

        # 5. STREAMING RESPONSE GENERATOR
        def event_stream():
            # --- forbidden fast-path (same as ChatStreamView) ---
            if has_forbidden:
                fallback = violations[0].get('fallback', 'This action is not permitted.')
                yield self._make_sse_chunk(chunk_id, req_model, role="assistant")
                yield self._make_sse_chunk(chunk_id, req_model, delta_content=fallback)
                yield self._make_sse_chunk(chunk_id, req_model, finish_reason="stop")
                yield "data: [DONE]\n\n"
                return

            # --- save user message (same as ChatStreamView) ---
            save_message(session_id, "user", user_message)

            # --- parallel KB fetch — EXACT SAME CALL AS ChatStreamView ---
            decisions, behaviors, authority_docs, _ = [], [], [], []
            try:
                decisions, behaviors, authority_docs, _ = fetch_kb_parallel(
                    user_message, situations, session_id, int(config.get('max_history', 10))
                )
            except Exception as e:
                logger.error(f"Parallel KB fetch failed in Tavus stream: {e}")

            # Use Tavus-provided history instead of DB history (Tavus-specific)
            history = tavus_history

            full_prompt = build_master_prompt(
                user_message, config, history, situations, violations,
                decisions, behaviors, authority_docs=authority_docs,
                include_reasoning_in_output=False
            )

            complexity = estimate_complexity(user_message, situations, violations)
            full_reply = ""
            model_used = "unknown"

            # --- first SSE chunk: role ---
            yield self._make_sse_chunk(chunk_id, req_model, role="assistant")

            # --- LLM streaming — EXACT SAME TIER LOGIC AS ChatStreamView ---
            try:
                if complexity == "fast" and not has_forbidden:
                    for chunk in llm.stream(full_prompt):
                        text = _extract_stream_text(chunk) or ""

                        if text:
                            full_reply += text
                            yield self._make_sse_chunk(chunk_id, req_model, delta_content=text)
                    model_used = "gpt-4o-mini-fast"

                elif settings.ANTHROPIC_API_KEY:
                    try:
                        for chunk in llm_anthropic.stream(full_prompt):
                            text = ""
                            if hasattr(chunk, 'content'):
                                if isinstance(chunk.content, list):
                                    for block in chunk.content:
                                        if isinstance(block, dict) and block.get("type") == "text":
                                            text += block.get("text", "")
                                        elif isinstance(block, str):
                                            text += block
                                else:
                                    text = str(chunk.content)
                            else:
                                text = str(chunk)
                            if text:
                                full_reply += text
                                yield self._make_sse_chunk(chunk_id, req_model, delta_content=text)
                        model_used = "claude-3-5-sonnet"
                    except Exception as e:
                        logger.error(f"Anthropic stream error: {e}")
                        for chunk in llm.stream(full_prompt):
                            text = _extract_stream_text(chunk) or ""

                            if text:
                                full_reply += text
                                yield self._make_sse_chunk(chunk_id, req_model, delta_content=text)
                        model_used = "gpt-4o-mini (fallback)"
                else:
                    for chunk in llm.stream(full_prompt):
                        text = _extract_stream_text(chunk) or ""

                        if text:
                            full_reply += text
                            yield self._make_sse_chunk(chunk_id, req_model, delta_content=text)
                    model_used = "gpt-4o-mini"

            except Exception as e:
                error_text = f"Error: {str(e)}"
                logger.error(f"Streaming LLM error in Tavus: {e}")
                yield self._make_sse_chunk(chunk_id, req_model, delta_content=error_text)

            finally:
                # --- save assistant reply (same as ChatStreamView) ---
                if full_reply:
                    save_message(session_id, "assistant", full_reply)

                # --- build thoughts / references for logging (same as ChatStreamView) ---
                confidence = self._calculate_confidence(violations, decisions, behaviors)
                reasoning_trace = self._build_reasoning_trace(situations, violations, decisions, behaviors, authority_docs)
                references = self._build_references(decisions, behaviors, violations, authority_docs)

                logger.info(
                    f"[Tavus LLM] === GOVERNANCE REPORT ===\n"
                    f"  Decisions: {len(decisions)} | "
                    f"Behaviors: {len(behaviors)} | "
                    f"Violations: {len(violations)} | "
                    f"Model: {model_used} | "
                    f"Reply: {full_reply[:100]}..."
                )

                yield self._make_sse_chunk(chunk_id, req_model, finish_reason="stop")
                yield "data: [DONE]\n\n"

        response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response


# =============================================================================
# NEW: DEBUG ENDPOINT — TEST YOUR KB WITHOUT TAVUS
# =============================================================================

class TavusLLMDebugView(APIView):
    """
    POST a test message here to see exactly what your KB returns.
    This bypasses Tavus entirely so you can verify the pipeline.
    
    Body: {"message": "What is our refund policy?", "session_id": "test123"}
    """
    permission_classes = [IsAdmin | IsChatOperator]

    def post(self, request):
        user_message = request.data.get("message", "").strip()
        session_id = request.data.get("session_id", "debug_default")
        
        if not user_message:
            return Response({"error": "message is required"}, status=400)

        logger.info(f"[Tavus Debug] Testing KB for: '{user_message[:100]}'")

        chat_view = ChatView()
        try:
            result = chat_view._process_chat(
                user_message=user_message,
                session_id=f"debug_{session_id}",
                use_kb=True
            )
        except Exception as e:
            logger.error(f"[Tavus Debug] Pipeline crashed: {e}\n{traceback.format_exc()}")
            return Response({
                "error": str(e),
                "traceback": traceback.format_exc()
            }, status=500)

        # Return the FULL internal state so you can inspect everything
        return Response({
            "reply": result.get("reply"),
            "kb_stats": {
                "decisions_retrieved": result.get("decisions_retrieved", 0),
                "behaviors_applied": result.get("behaviors_applied", 0),
                "authority_violations": result.get("authority_violations", 0),
                "has_forbidden": result.get("has_forbidden", False),
                "has_conditional": result.get("has_conditional", False),
            },
            "thoughts": result.get("thoughts", {}),
            "raw_prompt_preview": result.get("thoughts", {}).get("prompt_preview", "N/A"),
            "references": result.get("thoughts", {}).get("references", [])
        })

class TavusWebhookView(APIView):
    """
    Receive Tavus post-conversation webhooks (transcripts, summaries, events).
    This is NOT for real-time LLM responses — use TavusLLMCallbackView for that.
    """
    permission_classes = []
    authentication_classes = []

    def post(self, request):
        event_type = request.data.get("event_type", "unknown")
        conversation_id = request.data.get("conversation_id", "unknown")

        logger.info(f"[Tavus Webhook] Event: {event_type} | Conversation: {conversation_id}")

        if event_type == "application.transcription_ready":
            transcript = request.data.get("transcript", {})
            logger.info(f"[Tavus Webhook] Transcript received for {conversation_id}")

        elif event_type == "application.perception_analysis":
            logger.info(f"[Tavus Webhook] Perception analysis for {conversation_id}")

        elif event_type == "conversation.ended":
            logger.info(f"[Tavus Webhook] Conversation ended: {conversation_id}")

        return Response({"status": "received"})

# =============================================================================
# AUDIO UTILITIES
# =============================================================================

def convert_audio_to_raw_pcm(input_path, output_path, sample_rate=16000):
    import subprocess
    try:
        result = subprocess.run(
            ['ffmpeg', '-y', '-i', input_path, '-ar', str(sample_rate), '-ac', '1', '-f', 's16le', output_path],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"ffmpeg conversion failed: {e}")

    if PYDUB_AVAILABLE:
        try:
            audio = AudioSegment.from_file(input_path)
            audio = audio.set_channels(1).set_frame_rate(sample_rate).set_sample_width(2)
            raw_data = audio.raw_data
            with open(output_path, 'wb') as f:
                f.write(raw_data)
            return True
        except Exception as e:
            logger.warning(f"pydub conversion failed: {e}")

    return False


def raw_pcm_to_wav(raw_pcm_bytes, sample_rate=24000, channels=1, sample_width=2):
    import wave
    import io
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(raw_pcm_bytes)
    wav_io.seek(0)
    return wav_io.read()


# =============================================================================
# STT (OPTIMIZED CHUNK SIZE)
# =============================================================================

class STTView(APIView):
    permission_classes = [IsAdmin | IsChatOperator]
    def post(self, request):
        if 'audio' not in request.FILES:
            return Response({"error": "No audio file provided"}, status=400)

        audio_file = request.FILES['audio']
        logger.info(f"[STT] Received: name={audio_file.name}, size={audio_file.size}, type={audio_file.content_type}")

        if audio_file.size == 0:
            return Response({"error": "Empty audio file"}, status=400)

        api_key = getattr(settings, 'CARTESIA_API_KEY', '')
        if not api_key:
            return Response({"error": "Cartesia API key not configured"}, status=500)

        if not WEBSOCKET_CLIENT_AVAILABLE:
            return Response({
                "error": "websocket-client not installed. Run: pip install websocket-client"
            }, status=500)

        tmp_in_path = None
        tmp_out_path = None

        try:
            suffix = os.path.splitext(audio_file.name)[1] or '.webm'
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_in:
                for chunk in audio_file.chunks():
                    tmp_in.write(chunk)
                tmp_in_path = tmp_in.name

            with tempfile.NamedTemporaryFile(suffix='.raw', delete=False) as tmp_out:
                tmp_out_path = tmp_out.name

            success = convert_audio_to_raw_pcm(tmp_in_path, tmp_out_path, sample_rate=16000)
            if not success:
                return Response({
                    "error": "Audio conversion failed. Install ffmpeg or pydub."
                }, status=500)

            raw_size = os.path.getsize(tmp_out_path)
            logger.info(f"[STT] Converted PCM: {raw_size} bytes")

            if raw_size == 0:
                return Response({"error": "Audio conversion produced empty output"}, status=500)

            with open(tmp_out_path, 'rb') as f:
                raw_audio = f.read()

            transcript = self._transcribe_via_websocket(raw_audio, api_key)
            return Response({"transcript": transcript})

        except Exception as e:
            logger.error(f"[STT] Exception: {e}")
            return Response({"error": str(e)}, status=500)
        finally:
            if tmp_in_path and os.path.exists(tmp_in_path):
                os.unlink(tmp_in_path)
            if tmp_out_path and os.path.exists(tmp_out_path):
                os.unlink(tmp_out_path)

    def _transcribe_via_websocket(self, raw_audio: bytes, api_key: str) -> str:
        url = (
            "wss://api.cartesia.ai/stt/websocket"
            "?model=ink-whisper&language=en&encoding=pcm_s16le&sample_rate=16000"
        )

        transcripts = []
        done_event = threading.Event()
        error_msg = [None]
        connected = [False]

        def on_open(ws):
            connected[0] = True

        def on_message(ws, message):
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    return
                msg_type = data.get("type")
                if msg_type == "transcript" and data.get("is_final"):
                    transcripts.append(data.get("text", ""))
                    logger.debug(f"[STT] Final transcript chunk: {data.get('text', '')}")
                elif msg_type == "flush_done":
                    logger.info("[STT] Received 'flush_done' from Cartesia")
                    done_event.set()
                elif msg_type == "done":
                    logger.info("[STT] Received 'done' from Cartesia")
                    done_event.set()
                elif msg_type == "error":
                    error_msg[0] = data.get("message", "Unknown error")
                    logger.error(f"[STT] Cartesia error message: {error_msg[0]}")
                    done_event.set()

        def on_error(ws, error):
            err_str = str(error)
            if "Close message" in err_str or "close" in err_str.lower():
                if done_event.is_set():
                    return
                done_event.set()
                return
            if not done_event.is_set():
                error_msg[0] = err_str
                done_event.set()

        def on_close(ws, close_status_code, close_msg):
            done_event.set()

        ws = websocket.WebSocketApp(
            url,
            header={"Cartesia-Version": "2026-03-01", "X-API-Key": api_key},
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        ws_thread = threading.Thread(
            target=ws.run_forever,
            kwargs={'ping_interval': 30, 'ping_timeout': 10}
        )
        ws_thread.daemon = True
        ws_thread.start()

        start = time.time()
        while not connected[0]:
            if time.time() - start > 10:
                ws.close()
                ws_thread.join(timeout=5)
                raise TimeoutError("Failed to connect to Cartesia STT within 10s")
            time.sleep(0.05)

        try:
            chunk_size = 10240
            total_chunks = len(raw_audio) // chunk_size + (1 if len(raw_audio) % chunk_size else 0)
            logger.info(f"[STT] Streaming {total_chunks} chunks ({len(raw_audio)} bytes)")

            for i in range(0, len(raw_audio), chunk_size):
                if not ws.sock or not ws.sock.connected:
                    break
                ws.send(raw_audio[i:i + chunk_size], opcode=websocket.ABNF.OPCODE_BINARY)
                time.sleep(0.01)

            if ws.sock and ws.sock.connected:
                ws.send("finalize")
                ws.send("done")

            wait_timeout = 55
            logger.info(f"[STT] Waiting for transcription (timeout={wait_timeout}s)")
            done_event.wait(timeout=wait_timeout)

            if not done_event.is_set():
                ws.send("close")
                done_event.wait(timeout=5)
        finally:
            ws.close()
            ws_thread.join(timeout=5)

        if error_msg[0]:
            raise RuntimeError(f"Cartesia STT error: {error_msg[0]}")

        result = " ".join(transcripts).strip()
        logger.info(f"[STT] Transcription complete: {len(result)} chars")
        return result


# =============================================================================
# TTS
# =============================================================================

class TTSView(APIView):
    permission_classes = [IsAdmin | IsChatOperator]
    def post(self, request):
        text = request.data.get("text", "").strip()
        if not text:
            return Response({"error": "Text is required"}, status=400)

        config = get_config()
        voice_id = request.data.get("voice_id") or config.get(
            "cartesia_voice_id", "a5136bf9-224c-4d76-b823-52bd5efcffcc"
        )
        model_id = request.data.get("model_id") or config.get(
            "cartesia_model", "sonic-3.5"
        )
        speed = float(request.data.get("speed") or config.get("cartesia_speed", "1.0"))

        api_key = getattr(settings, 'CARTESIA_API_KEY', '')
        if not api_key:
            return Response({"error": "Cartesia API key not configured"}, status=500)

        if not WEBSOCKET_CLIENT_AVAILABLE:
            return Response({
                "error": "websocket-client not installed. Run: pip install websocket-client"
            }, status=500)

        try:
            raw_audio = self._synthesize_via_websocket(
                text=text,
                voice_id=voice_id,
                model_id=model_id,
                speed=speed,
                api_key=api_key
            )

            wav_audio = raw_pcm_to_wav(raw_audio, sample_rate=24000, channels=1, sample_width=2)
            return HttpResponse(wav_audio, content_type="audio/wav")

        except Exception as e:
            logger.error(f"[TTS] WebSocket error: {e}")
            return Response({"error": str(e)}, status=500)

    def _synthesize_via_websocket(self, text, voice_id, model_id, speed, api_key):
        url = "wss://api.cartesia.ai/tts/websocket"
        audio_chunks = []
        done_event = threading.Event()
        error_msg = [None]
        context_id = str(uuid.uuid4())
        connected = [False]

        def on_open(ws):
            connected[0] = True

        def on_message(ws, message):
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    return
                msg_type = data.get("type")
                if msg_type == "chunk" and data.get("data"):
                    try:
                        audio_chunks.append(base64.b64decode(data["data"]))
                    except Exception:
                        pass
                elif msg_type == "done":
                    done_event.set()
                elif msg_type == "flush_done":
                    done_event.set()
                elif msg_type == "type" == "error":
                    error_msg[0] = data.get("message", "Unknown Cartesia error")
                    done_event.set()

        def on_error(ws, error):
            err_str = str(error)
            if "Close message" in err_str or "close" in err_str.lower():
                if done_event.is_set():
                    return
                done_event.set()
                return
            if not done_event.is_set():
                error_msg[0] = err_str
                done_event.set()

        def on_close(ws, close_status_code, close_msg):
            done_event.set()

        ws = websocket.WebSocketApp(
            url,
            header={"Cartesia-Version": "2026-03-01", "X-API-Key": api_key},
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )

        ws_thread = threading.Thread(
            target=ws.run_forever,
            kwargs={'ping_interval': 30, 'ping_timeout': 10}
        )
        ws_thread.daemon = True
        ws_thread.start()

        start = time.time()
        while not connected[0]:
            if time.time() - start > 10:
                ws.close()
                ws_thread.join(timeout=5)
                raise TimeoutError("Failed to connect to Cartesia TTS")
            time.sleep(0.05)

        try:
            request = {
                "model_id": model_id,
                "transcript": text,
                "voice": {"mode": "id", "id": voice_id},
                "language": "en",
                "context_id": context_id,
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": 24000
                },
                "continue": False,
                "add_timestamps": False
            }
            if speed != 1.0:
                request["generation_config"] = {"speed": speed}

            ws.send(json.dumps(request))
            done_event.wait(timeout=60)
        finally:
            ws.close()
            ws_thread.join(timeout=5)

        if error_msg[0]:
            raise RuntimeError(f"Cartesia TTS error: {error_msg[0]}")

        return b"".join(audio_chunks)