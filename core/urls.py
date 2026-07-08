from django.urls import path, include
from rest_framework.routers import SimpleRouter
from . import views

router = SimpleRouter(trailing_slash=False)
router.register(r'decisions', views.DecisionViewSet, basename='decisions')
router.register(r'behaviors', views.BehaviorViewSet, basename='behaviors')
router.register(r'authority', views.AuthorityViewSet, basename='authority')
router.register(r'flags', views.FlagViewSet, basename='flags')

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('health', views.HealthView.as_view(), name='health'),
    path('api/config', views.ConfigView.as_view(), name='config'),
    path('api/chat', views.ChatView.as_view(), name='chat'),
    path('api/history', views.HistoryView.as_view(), name='history'),
    path('api/clear', views.ClearView.as_view(), name='clear'),
    path('api/stats', views.StatsView.as_view(), name='stats'),
    path('api/stt', views.STTView.as_view(), name='stt'),
    path('api/tts', views.TTSView.as_view(), name='tts'),
    path('api/', include(router.urls)),
]