"""
URL configuration for the MaziwaLog project.

Routes:
    /admin/         Django admin
    /accounts/      django-allauth (email + Google OAuth 2.0)
    /api/v1/        Django REST Framework endpoints
    /               MaziwaLog web dashboard (myapp.urls)
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("api/v1/", include("myapp.api.urls")),
    path("", include("myapp.urls")),
]
