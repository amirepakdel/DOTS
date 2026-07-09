from django.urls import path
from . import consumers
websocket_urlpatterns = [
    path("ws/api/stt/", consumers.CartesiaSTTConsumer.as_asgi()),
    path("ws/api/tts/", consumers.CartesiaTTSConsumer.as_asgi()),
]