"""
Iraniu — URL configuration.
Media: served publicly (no login_required). Middleware allows /media/ without auth for Instagram crawler.
Django static() does not expose directory listing; production should serve media via Nginx/Apache with autoindex off.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include
from django.views.i18n import set_language

urlpatterns = [
    path('admin/', admin.site.urls),
    path('i18n/setlang/', set_language, name='set_language'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', include('core.urls')),
]

# Media: public read-only. Not wrapped in any login_required; middleware bypasses auth for /media/
if settings.DEBUG and settings.MEDIA_URL:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
