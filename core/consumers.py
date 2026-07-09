import json
import uuid
import base64
import asyncio
import logging
import struct
import io
import wave

from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

logger = logging.getLogger(__name__)


def raw_pcm_to_wav(raw_pcm_bytes, sample_rate=24000, channels=1, sample_width=2):
    """Wrap raw PCM data in a WAV header."""
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(raw_pcm_bytes)
    wav_io.seek(0)
    return wav_io.read()


class CartesiaSTTConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer proxying browser audio -> Cartesia STT WebSocket.
    """

    CARTESIA_WS_URL = "wss://api.cartesia.ai/stt/websocket"

    async def connect(self):
        self.cartesia_ws = None
        self.transcript_parts = []
        self.connected = False
        await self.accept()

    async def disconnect(self, close_code):
        if self.cartesia_ws:
            try:
                await self.cartesia_ws.close()
            except Exception:
                pass
        self.connected = False

    async def receive(self, text_data=None, bytes_data=None):
        if not self.connected and text_data:
            try:
                config = json.loads(text_data)
                if config.get("type") == "config":
                    await self._connect_to_cartesia(config)
                    return
            except json.JSONDecodeError:
                pass

        if not self.cartesia_ws:
            await self.send_json({
                "type": "error",
                "error": "Not connected to Cartesia. Send config first."
            })
            return

        try:
            if bytes_data:
                await self.cartesia_ws.send(bytes_data)
            elif text_data:
                if text_data in ("finalize", "close"):
                    await self.cartesia_ws.send(text_data)
                    if text_data == "finalize":
                        await self.cartesia_ws.send("done")   
                else:
                    try:
                        json.loads(text_data)
                        await self.cartesia_ws.send(text_data)
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            logger.error(f"STT relay error: {e}")
            await self.send_json({"type": "error", "error": str(e)})

    async def _connect_to_cartesia(self, config):
        if not WEBSOCKETS_AVAILABLE:
            await self.send_json({"type": "error", "error": "websockets library not installed on server"})
            await self.close()
            return

        api_key = getattr(settings, 'CARTESIA_API_KEY', '')
        if not api_key:
            await self.send_json({"type": "error", "error": "CARTESIA_API_KEY not configured"})
            await self.close()
            return

        model = config.get("model", "ink-whisper")
        language = config.get("language", "en")
        encoding = config.get("encoding", "pcm_s16le")
        sample_rate = config.get("sample_rate", 16000)

        url = (
            f"{self.CARTESIA_WS_URL}"
            f"?model={model}&language={language}&encoding={encoding}&sample_rate={sample_rate}"
        )

        extra_headers = {
            "Cartesia-Version": "2026-03-01",
            "X-API-Key": api_key,
        }

        try:
            self.cartesia_ws = await websockets.connect(url, additional_headers=extra_headers)
            self.connected = True
            await self.send_json({
                "type": "ready",
                "message": "Connected to Cartesia STT. Start sending audio chunks."
            })
            asyncio.create_task(self._relay_cartesia_to_client())
        except Exception as e:
            logger.error(f"STT connect error: {e}")
            await self.send_json({"type": "error", "error": str(e)})
            await self.close()

    async def _relay_cartesia_to_client(self):
        """
        Relay audio from Cartesia back to the browser.
        Buffers audio per context_id and sends complete sentence audio
        only when Cartesia signals 'done'.
        """
        try:
            async for message in self.cartesia_ws:
                if isinstance(message, str):
                    data = json.loads(message)
                    msg_type = data.get("type")
                    context_id = data.get("context_id")

                    if msg_type == "chunk" and data.get("data"):
                        audio_bytes = base64.b64decode(data["data"])
                        # Accumulate in buffer instead of streaming immediately
                        if context_id and context_id in self.active_contexts:
                            self.active_contexts[context_id]["buffer"].extend(audio_bytes)

                    elif msg_type == "done":
                        # Send complete accumulated audio for this sentence
                        if context_id and context_id in self.active_contexts:
                            buffer = self.active_contexts[context_id]["buffer"]
                            if buffer:
                                await self.send(bytes_data=bytes(buffer))
                            del self.active_contexts[context_id]
                        
                        # Signal completion to frontend
                        await self.send_json({
                            "type": "done",
                            "context_id": context_id,
                            "status_code": data.get("status_code")
                        })

                    elif msg_type == "timestamps":
                        await self.send_json({
                            "type": "timestamps",
                            "context_id": context_id,
                            "word_timestamps": data.get("word_timestamps")
                        })

                    elif msg_type == "error":
                        await self.send_json({
                            "type": "error",
                            "context_id": context_id,
                            "error": data.get("message", "Unknown Cartesia error")
                        })

                else:
                    # Rare: binary frame from Cartesia
                    pass

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f"TTS relay task error: {e}")
        finally:
            await self.send_json({"type": "connection_closed"})

    async def send_json(self, data):
        await self.send(text_data=json.dumps(data))


class CartesiaTTSConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer proxying browser text -> Cartesia TTS WebSocket.
    Supports streaming text chunks for ultra-low latency.
    """

    CARTESIA_WS_URL = "wss://api.cartesia.ai/tts/websocket"

    async def connect(self):
        self.cartesia_ws = None
        self.connected = False
        self.relay_task = None
        # Track active contexts so we can stream multiple utterances
        self.active_contexts = {}
        await self.accept()

    async def disconnect(self, close_code):
        if self.relay_task and not self.relay_task.done():
            self.relay_task.cancel()
        if self.cartesia_ws:
            try:
                await self.cartesia_ws.close()
            except Exception:
                pass
        self.connected = False

    async def receive(self, text_data=None, bytes_data=None):
        # --- Step 1: Config / Connection ---
        if not self.connected and text_data:
            try:
                config = json.loads(text_data)
                if config.get("type") == "config":
                    await self._connect_to_cartesia(config)
                    return
            except json.JSONDecodeError:
                pass

        if not self.cartesia_ws:
            await self.send_json({
                "type": "error",
                "error": "Not connected to Cartesia. Send config first."
            })
            return

        # --- Step 2: Handle client messages ---
        try:
            data = json.loads(text_data) if text_data else {}

            msg_type = data.get("type")

            # ═══════════════════════════════════════
            # MODE A: Single-shot (backward compatible)
            # ═══════════════════════════════════════
            if msg_type == "generate" or "transcript" in data:
                context_id = data.get("context_id", str(uuid.uuid4()))
                request = {
                    "model_id": data.get("model_id", "sonic-3.5"),
                    "transcript": data.get("transcript", ""),
                    "voice": {
                        "mode": "id",
                        "id": data.get("voice_id", "a5136bf9-224c-4d76-b823-52bd5efcffcc")
                    },
                    "language": data.get("language", "en"),
                    "context_id": context_id,
                    "output_format": {
                        "container": "raw",
                        "encoding": "pcm_s16le",
                        "sample_rate": 24000
                    },
                    "continue": False,
                    "add_timestamps": data.get("add_timestamps", False)
                }
                speed = data.get("speed")
                if speed is not None:
                    request["generation_config"] = {"speed": float(speed)}

                await self.cartesia_ws.send(json.dumps(request))
                return

            # ═══════════════════════════════════════
            # MODE B: Streaming chunks (THE FAST WAY)
            # ═══════════════════════════════════════
            if msg_type == "chunk":
                context_id = data.get("context_id")
                if not context_id:
                    await self.send_json({
                        "type": "error",
                        "error": "context_id is required for streaming chunks"
                    })
                    return

                text_chunk = data.get("text", "")
                is_continue = data.get("continue", True)  # default true for streaming
                is_final = not is_continue

                request = {
                    "model_id": data.get("model_id", "sonic-3.5"),
                    "transcript": text_chunk,
                    "voice": {
                        "mode": "id",
                        "id": data.get("voice_id", "a5136bf9-224c-4d76-b823-52bd5efcffcc")
                    },
                    "language": data.get("language", "en"),
                    "context_id": context_id,
                    "output_format": {
                        "container": "raw",
                        "encoding": "pcm_s16le",
                        "sample_rate": 24000
                    },
                    "continue": is_continue,
                    "add_timestamps": data.get("add_timestamps", False)
                }
                speed = data.get("speed")
                if speed is not None:
                    request["generation_config"] = {"speed": float(speed)}

                # Optional: tune buffer delay for low latency
                # Lower = faster audio start, but higher risk of cutoffs
                max_buffer = data.get("max_buffer_delay_ms")
                if max_buffer is not None:
                    request["max_buffer_delay_ms"] = int(max_buffer)

                await self.cartesia_ws.send(json.dumps(request))

                # Track active context
                self.active_contexts[context_id] = {
                    "finalized": is_final,
                    "buffer": bytearray()
                }

                await self.send_json({
                    "type": "chunk_ack",
                    "context_id": context_id,
                    "chars_sent": len(text_chunk),
                    "continue": is_continue
                })
                return

            # ═══════════════════════════════════════
            # MODE C: Flush / finalize a context
            # ═══════════════════════════════════════
            if msg_type == "finalize":
                context_id = data.get("context_id")
                if context_id and context_id in self.active_contexts:
                    # Send empty transcript with continue=false to flush
                    await self.cartesia_ws.send(json.dumps({
                        "model_id": data.get("model_id", "sonic-3.5"),
                        "transcript": "",
                        "voice": {
                            "mode": "id",
                            "id": data.get("voice_id", "a5136bf9-224c-4d76-b823-52bd5efcffcc")
                        },
                        "language": data.get("language", "en"),
                        "context_id": context_id,
                        "output_format": {
                            "container": "raw",
                            "encoding": "pcm_s16le",
                            "sample_rate": 24000
                        },
                        "continue": False,
                        "add_timestamps": False
                    }))
                    self.active_contexts[context_id]["finalized"] = True
                return

            # ═══════════════════════════════════════
            # MODE D: Cancel ongoing generation
            # ═══════════════════════════════════════
            if msg_type == "cancel":
                context_id = data.get("context_id")
                if context_id:
                    await self.cartesia_ws.send(json.dumps({
                        "context_id": context_id,
                        "cancel": True
                    }))
                return

            # ═══════════════════════════════════════
            # MODE E: Close connection
            # ═══════════════════════════════════════
            if msg_type == "close":
                await self.close()
                return

        except Exception as e:
            logger.error(f"TTS relay error: {e}")
            await self.send_json({"type": "error", "error": str(e)})

    async def _connect_to_cartesia(self, config):
        if not WEBSOCKETS_AVAILABLE:
            await self.send_json({"type": "error", "error": "websockets library not installed"})
            await self.close()
            return

        api_key = getattr(settings, 'CARTESIA_API_KEY', '')
        if not api_key:
            await self.send_json({"type": "error", "error": "CARTESIA_API_KEY not configured"})
            await self.close()
            return

        extra_headers = {
            "Cartesia-Version": config.get("api_version", "2026-03-01"),
            "X-API-Key": api_key,
        }

        try:
            self.cartesia_ws = await websockets.connect(
                self.CARTESIA_WS_URL,
                additional_headers=extra_headers
            )
            self.connected = True
            await self.send_json({
                "type": "ready",
                "message": "Connected to Cartesia TTS. Send generate or chunk requests."
            })
            self.relay_task = asyncio.create_task(self._relay_cartesia_to_client())
        except Exception as e:
            logger.error(f"TTS connect error: {e}")
            await self.send_json({"type": "error", "error": str(e)})
            await self.close()

    async def _relay_cartesia_to_client(self):
        """
        Relay audio + metadata from Cartesia back to the browser in real time.
        """
        try:
            async for message in self.cartesia_ws:
                if isinstance(message, str):
                    data = json.loads(message)
                    msg_type = data.get("type")

                    if msg_type == "chunk" and data.get("data"):
                        audio_bytes = base64.b64decode(data["data"])
                        context_id = data.get("context_id")

                        # Optionally accumulate per-context for later download
                        if context_id and context_id in self.active_contexts:
                            self.active_contexts[context_id]["buffer"].extend(audio_bytes)

                        # ── STREAM AUDIO IMMEDIATELY ──
                        await self.send(bytes_data=audio_bytes)

                        # Send metadata so the client knows progress
                        await self.send_json({
                            "type": "chunk",
                            "context_id": context_id,
                            "done": data.get("done", False),
                            "audio_bytes": len(audio_bytes)
                        })

                    elif msg_type == "timestamps":
                        await self.send_json({
                            "type": "timestamps",
                            "context_id": data.get("context_id"),
                            "word_timestamps": data.get("word_timestamps")
                        })

                    elif msg_type == "done":
                        await self.send_json({
                            "type": "done",
                            "context_id": data.get("context_id"),
                            "status_code": data.get("status_code")
                        })

                    elif msg_type == "error":
                        await self.send_json({
                            "type": "error",
                            "context_id": data.get("context_id"),
                            "error": data.get("message", "Unknown Cartesia error")
                        })

                else:
                    # Rare: binary frame from Cartesia
                    await self.send(bytes_data=message)

        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f"TTS relay task error: {e}")
        finally:
            await self.send_json({"type": "connection_closed"})

    async def send_json(self, data):
        await self.send(text_data=json.dumps(data))