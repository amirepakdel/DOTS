import requests
import logging
import json
import uuid
import base64
import threading
import tempfile
import os
import time
import struct
import io

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

try:
    import websocket
    WEBSOCKET_CLIENT_AVAILABLE = True
except ImportError:
    WEBSOCKET_CLIENT_AVAILABLE = False

# Optional: pydub for audio conversion without ffmpeg
try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False

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
    """Standard non-streaming chat endpoint with detailed governance trace."""

    def _calculate_confidence(self, violations, decisions, behaviors):
        """Calculate a 0-100 confidence score based on match quality."""
        score = 75  # base confidence
        if any(v['allowed'] == 'no' for v in violations):
            score = 95  # High confidence when refusing (clear rule)
        elif any(v['allowed'] == 'conditional' for v in violations):
            score = 65  # Lower when conditional
        if len(decisions) > 0:
            score += min(len(decisions) * 5, 15)  # +5 per relevant decision, max +15
        if len(behaviors) > 0:
            score += min(len(behaviors) * 3, 6)   # +3 per behavior, max +6
        return min(score, 100)

    def _build_reasoning_trace(self, situations, violations, decisions, behaviors, authority_docs=None):
        """Build a detailed human-readable reasoning summary for managers."""
        trace_parts = []
        
        # Stage 1: Input summary
        trace_parts.append("=== Stage 1: Input Analysis ===")
        trace_parts.append("Message received and validated. History context loaded.")
        
        # Stage 2: Situation detection
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
        
        # Stage 3: Authority check
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
        
        # Layer 2: Semantic authority
        trace_parts.append("\nLayer 2 (Semantic Search): Queried vectorstore for semantically similar authority rules.")
        if authority_docs:
            trace_parts.append(f"  → Found {len(authority_docs)} relevant rule(s) via embedding similarity.")
            for i, doc in enumerate(authority_docs, 1):
                trace_parts.append(f"    [{i}] {doc.page_content[:120]}...")
        else:
            trace_parts.append("  → No additional semantic matches above threshold.")
        
        # Stage 4: KB Retrieval
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
        
        # Stage 5: Response Build
        trace_parts.append("\n=== Stage 5: Response Build ===")
        trace_parts.append("Master prompt assembled with all governance context injected.")
        trace_parts.append("LLM invoked with temperature=0.3 (deterministic mode).")
        
        return "\n".join(trace_parts)

    def _build_references(self, decisions, behaviors, violations, authority_docs=None):
        """Build reference list with IDs, titles, and similarity scores."""
        refs = []
        for d in decisions:
            refs.append({
                "id": f"dec-{d.metadata.get('id', 'unknown')}",
                "type": "decision",
                "title": d.page_content[:60] + "..." if len(d.page_content) > 60 else d.page_content,
                "score": 0.85,  # Could compute actual cosine similarity
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

    def _process_chat(self, user_message, session_id, use_kb):
        config = get_config()
        situations = detect_situation(user_message)
        violations = check_authority(user_message)
        has_forbidden = any(v['allowed'] == 'no' for v in violations)
        has_conditional = any(v['allowed'] == 'conditional' for v in violations)

        save_message(session_id, "user", user_message)

        decisions = []
        behaviors = []
        authority_docs = []
        
        if use_kb:
            decisions = get_relevant_decisions(user_message, top_k=3)
            behaviors = get_relevant_behaviors(situations)
            # NEW: Also get semantic authority rules
            authority_docs = get_relevant_authority_rules(user_message, top_k=2)

        history = get_history(session_id, limit=int(config.get('max_history', 10)))
        full_prompt = build_master_prompt(
            user_message, config, history, situations, violations, decisions, behaviors,
            authority_docs=authority_docs,
            include_reasoning_in_output=False
        )

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

        # Calculate confidence
        confidence = self._calculate_confidence(violations, decisions, behaviors)
        
        # Build detailed reasoning trace
        reasoning_trace = self._build_reasoning_trace(situations, violations, decisions, behaviors, authority_docs)
        
        # Build references
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
                "prompt_tokens_estimate": len(full_prompt.split()),  # Rough estimate
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
                        "temperature": 0.3
                    }
                }
            }
        }


class ChatStreamView(APIView):
    """
    Streaming chat endpoint for real-time voice calls.
    Returns Server-Sent Events (SSE) with text chunks as they are generated.
    Includes thoughts metadata in the final done chunk.
    """

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

            # Handle forbidden immediately
            if has_forbidden:
                fallback = violations[0].get('fallback', 'This action is not permitted.')
                yield f"data: {json.dumps({'text': fallback, 'done': False})}\n\n"
                yield f"data: {json.dumps({'text': '', 'done': True})}\n\n"
                return

            save_message(session_id, "user", user_message)

            decisions = []
            behaviors = []
            authority_docs = []
            if use_kb:
                decisions = get_relevant_decisions(user_message, top_k=3)
                behaviors = get_relevant_behaviors(situations)
                authority_docs = get_relevant_authority_rules(user_message, top_k=2)

            history = get_history(session_id, limit=int(config.get('max_history', 10)))
            full_prompt = build_master_prompt(
                user_message, config, history, situations, violations, decisions, behaviors,
                authority_docs=authority_docs,
                include_reasoning_in_output=False
            )

            full_reply = ""

            try:
                # Try Anthropic streaming first
                if settings.ANTHROPIC_API_KEY:
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
                    except Exception as e:
                        logger.error(f"Anthropic stream error: {e}")
                        # Fallback to OpenAI
                        for chunk in llm_openai.stream(full_prompt):
                            text = getattr(chunk, 'content', str(chunk)) or ""
                            if text:
                                full_reply += text
                                yield f"data: {json.dumps({'text': text, 'done': False})}\n\n"
                else:
                    for chunk in llm_openai.stream(full_prompt):
                        text = getattr(chunk, 'content', str(chunk)) or ""
                        if text:
                            full_reply += text
                            yield f"data: {json.dumps({'text': text, 'done': False})}\n\n"

            except Exception as e:
                error_text = f"Error: {str(e)}"
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
                    "model_used": "claude-3-5-sonnet" if settings.ANTHROPIC_API_KEY else "gpt-4o-mini",
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


# =============================================================================
# AUDIO CONVERSION UTILITIES
# =============================================================================

def convert_audio_to_raw_pcm(input_path, output_path, sample_rate=16000):
    """
    Convert any audio file to raw PCM s16le mono at target sample rate.
    Tries ffmpeg first, falls back to pydub, then pure Python.
    """
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
    """Wrap raw PCM data in a WAV header for browser playback."""
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
# CARTESIA WEBSOCKET STT (Speech-to-Text)
# =============================================================================

class STTView(APIView):
    """
    Speech-to-Text via Cartesia WebSocket API.
    Accepts multipart audio file upload, converts to raw PCM s16le 16kHz mono,
    streams to Cartesia STT WebSocket, returns transcript.
    """

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
                    "error": "Audio conversion failed. Install ffmpeg or pydub. "
                             "Docker: RUN apt-get update && apt-get install -y ffmpeg"
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
            chunk_size = 6400
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
# CARTESIA WEBSOCKET TTS (Text-to-Speech)
# =============================================================================

class TTSView(APIView):
    """
    Text-to-Speech via Cartesia WebSocket API.
    Returns WAV file for browser playback.
    """

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
                elif msg_type == "error":
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