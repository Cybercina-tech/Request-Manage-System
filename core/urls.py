"""
Iranio — Core URL routes.
"""

from django.urls import path
from . import views
from . import telegram_views

urlpatterns = [
    path('', views.landing, name='landing'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('requests/', views.ad_list, name='ad_list'),
    path('requests/<uuid:uuid>/', views.ad_detail, name='ad_detail'),
    path('bots/', views.bot_list, name='bot_list'),
    path('bots/create/', views.bot_create, name='bot_create'),
    path('bots/<int:pk>/edit/', views.bot_edit, name='bot_edit'),
    path('bots/<int:pk>/delete/', views.bot_delete, name='bot_delete'),
    path('bots/<int:pk>/test/', views.bot_test, name='bot_test'),
    path('bots/<int:pk>/regenerate-webhook/', views.bot_regenerate_webhook, name='bot_regenerate_webhook'),
    path('settings/', views.settings_view, name='settings'),
    path('settings/save/', views.settings_save, name='settings_save'),
    path('settings/test-telegram/', views.test_telegram, name='test_telegram'),
    path('settings/test-openai/', views.test_openai, name='test_openai'),
    path('settings/export/', views.export_config, name='export_config'),
    path('settings/import/', views.import_config, name='import_config'),
    path('api/approve/', views.approve_ad, name='approve_ad'),
    path('api/reject/', views.reject_ad, name='reject_ad'),
    path('api/bulk-approve/', views.bulk_approve, name='bulk_approve'),
    path('api/bulk-reject/', views.bulk_reject, name='bulk_reject'),
    path('api/pulse/', views.api_pulse, name='api_pulse'),
    path('api/submit/', views.submit_ad, name='submit_ad'),
    path('telegram/webhook/<int:bot_id>/', telegram_views.telegram_webhook, name='telegram_webhook'),
]
