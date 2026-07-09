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


# =============================================================================
# AUDIO CONVERSION UTILITIES
# =============================================================================

def convert_audio_to_raw_pcm(input_path, output_path, sample_rate=16000):
    """
    Convert any audio file to raw PCM s16le mono at target sample rate.
    Tries ffmpeg first, falls back to pydub, then pure Python.
    """
    # Try ffmpeg first (best quality)
    import subprocess
    try:
        result = subprocess.run(
            ['ffmpeg', '-y', '-i', input_path, '-ar', str(sample_rate), '-ac', '1', '-f', 's16le', output_path],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return True
    except FileNotFoundError:
        pass  # ffmpeg not installed
    except Exception as e:
        logger.warning(f"ffmpeg conversion failed: {e}")

    # Fallback to pydub
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

    WARNING: This blocks a gunicorn sync worker for the entire STT session.
    For production, use the async WebSocket consumer (CartesiaSTTConsumer) instead.
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

            # Convert to raw PCM
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
                elif msg_type in ("done", "flush_done"):
                    logger.info("[STT] Received 'done' from Cartesia")
                    done_event.set()
                elif msg_type == "error":
                    error_msg[0] = data.get("message", "Unknown error")
                    logger.error(f"[STT] Cartesia error message: {error_msg[0]}")
                    done_event.set()

        def on_error(ws, error):
            err_str = str(error)
            # Ignore normal close frames and errors after we're already done
            if "Close message" in err_str or "close" in err_str.lower():
                if done_event.is_set():
                    logger.debug(f"[STT] Ignoring close-frame error after done: {err_str}")
                    return
                # If not done, treat as signal to finish
                logger.warning(f"[STT] Connection closed unexpectedly: {err_str}")
                done_event.set()
                return
            if not done_event.is_set():
                error_msg[0] = err_str
                logger.error(f"[STT] WebSocket error: {err_str}")
                done_event.set()

        def on_close(ws, close_status_code, close_msg):
            logger.info(f"[STT] WebSocket closed: code={close_status_code}, msg={close_msg}")
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

        # Wait for connection with timeout
        start = time.time()
        while not connected[0]:
            if time.time() - start > 10:
                ws.close()
                ws_thread.join(timeout=5)
                raise TimeoutError("Failed to connect to Cartesia STT within 10s")
            time.sleep(0.05)

        try:
            # Stream audio chunks faster (larger chunks, less sleep)
            chunk_size = 6400  # doubled from 3200
            total_chunks = len(raw_audio) // chunk_size + (1 if len(raw_audio) % chunk_size else 0)
            logger.info(f"[STT] Streaming {total_chunks} chunks ({len(raw_audio)} bytes)")

            for i in range(0, len(raw_audio), chunk_size):
                if not ws.sock or not ws.sock.connected:
                    logger.warning("[STT] WebSocket disconnected during streaming")
                    break
                ws.send(raw_audio[i:i + chunk_size], opcode=websocket.ABNF.OPCODE_BINARY)
                # Adaptive sleep: shorter for more chunks
                time.sleep(0.01)

            if ws.sock and ws.sock.connected:
                logger.info("[STT] Sending finalize")
                ws.send("finalize")
                ws.send("done")

            # Wait longer for transcription — must be < gunicorn timeout
            wait_timeout = 55  # seconds; keep this < gunicorn timeout
            logger.info(f"[STT] Waiting for transcription (timeout={wait_timeout}s)")
            done_event.wait(timeout=wait_timeout)

            if not done_event.is_set():
                logger.warning("[STT] Timeout waiting for 'done', sending close")
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

    IMPORTANT: Cartesia TTS WebSocket only supports 'raw' container.
    We request raw PCM s16le 24kHz, then wrap it in a WAV header for browser playback.
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
            # Cartesia WS TTS only supports raw PCM
            raw_audio = self._synthesize_via_websocket(
                text=text,
                voice_id=voice_id,
                model_id=model_id,
                speed=speed,
                api_key=api_key
            )

            # Wrap raw PCM in WAV header for browser playback
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