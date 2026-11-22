# Web URL routing for MaziwaLog.

from django.urls import path

from . import views

urlpatterns = [
    # Liveness probe for the uptime monitor.
    path("health/", views.health, name="health"),
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("complete-registration/", views.complete_registration, name="complete_registration"),
    path("dashboard/manager/", views.manager_home, name="manager_home"),
    # Intake ledger (clerk)
    path("intake/", views.clerk_intake, name="clerk_intake"),
    # Farmer portal
    path("portal/", views.farmer_portal, name="farmer_portal"),
    # Farmers
    path("farmers/", views.farmer_list, name="farmer_list"),
    path("farmers/add/", views.farmer_create, name="farmer_create"),
    path("farmers/<int:pk>/", views.farmer_detail, name="farmer_detail"),
    # Store credit book
    path("store-credit/", views.store_credit_list, name="store_credit_list"),
    path("store-credit/add/", views.store_credit_create, name="store_credit_create"),
    # Month-end statements
    path("statements/", views.statement_list, name="statement_list"),
    path("statements/generate/", views.statement_generate, name="statement_generate"),
    path(
        "statements/<int:pk>/mark-paid/",
        views.statement_mark_paid,
        name="statement_mark_paid",
    ),
    path("statements/<int:pk>/pdf/", views.statement_pdf, name="statement_pdf"),
    path("statements/<int:pk>/preview/", views.statement_preview, name="statement_preview"),
    # Daraja payments
    path("payments/stk-push/", views.payment_stk_push, name="payment_stk_push"),
    path(
        "payments/status/<str:checkout_id>/",
        views.payment_status_page,
        name="payment_status_page",
    ),
    path(
        "payments/status/<str:checkout_id>/poll/",
        views.payment_status_api,
        name="payment_status_api",
    ),
    path("payments/daraja/callback/", views.daraja_callback, name="daraja_callback"),
    # Cooler settings & workspace switching
    path("cooler/settings/", views.cooler_settings, name="cooler_settings"),
    path("cooler/switch/", views.switch_cooler, name="switch_cooler"),
]
