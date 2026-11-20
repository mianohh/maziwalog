"""REST API routes mounted at /api/v1/."""

from django.urls import path

from . import views

urlpatterns = [
    path("auth/token/", views.ObtainToken.as_view(), name="api_token"),
    path("intake/", views.IntakeListCreate.as_view(), name="api_intake"),
    path(
        "farmers/<str:member_no>/summary/",
        views.FarmerSummary.as_view(),
        name="api_farmer_summary",
    ),
    path("payments/stk-push/", views.StkPush.as_view(), name="api_stk_push"),
    path(
        "payments/status/<str:checkout_id>/",
        views.PaymentStatus.as_view(),
        name="api_payment_status",
    ),
]
