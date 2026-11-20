"""
REST API views.

Endpoints
    POST /api/v1/intake/                       Clerk, Manager
    GET  /api/v1/farmers/<member_no>/summary/  All roles
    POST /api/v1/payments/stk-push/            Farmer, Clerk
    GET  /api/v1/payments/status/<checkout_id>/ All roles
    POST /api/v1/auth/token/                   Email + password -> API token

Every queryset is filtered by the active cooler resolved by
ActiveCoolerMiddleware, so a token from Cooler A cannot read Cooler B data.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import services
from ..daraja import DarajaClient, DarajaError
from ..models import Farmer, Membership, MilkIntake, MpesaTransaction
from ..views import _initiate_stk, _require_cooler
from .permissions import FARMER_CLERK, MANAGER_CLERK, HasRole, IsCoolerMember
from .serializers import (
    FarmerSummarySerializer,
    IntakeCreateSerializer,
    MilkIntakeSerializer,
    PaymentStatusSerializer,
    STKPushSerializer,
)


class ObtainToken(APIView):
    """POST {email, password} -> {token, user}. Session-free API access."""

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        email = (request.data.get("email") or "").strip()
        password = request.data.get("password") or ""
        user = authenticate(request, username=email, password=password)
        if user is None:
            return Response(
                {"detail": "Invalid email or password."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {
                "token": token.key,
                "email": user.email,
                "full_name": user.full_name,
            }
        )


class IntakeListCreate(APIView):
    """GET lists the workspace ledger, POST records a delivery (Clerk, Manager)."""

    permission_classes = [HasRole]
    allowed_roles = MANAGER_CLERK

    def get(self, request):
        cooler = _require_cooler(request)
        qs = MilkIntake.objects.filter(cooler=cooler).select_related(
            "farmer", "recorded_by"
        )
        collection_date = request.query_params.get("date")
        if collection_date:
            qs = qs.filter(collection_date=collection_date)
        session = request.query_params.get("session")
        if session:
            qs = qs.filter(session=session)
        serializer = MilkIntakeSerializer(qs[:200], many=True)
        return Response(serializer.data)

    def post(self, request):
        cooler = _require_cooler(request)
        serializer = IntakeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        farmer = get_object_or_404(
            Farmer, cooler=cooler, member_number=data["member_number"], is_active=True
        )
        intake = MilkIntake(
            cooler=cooler,
            farmer=farmer,
            collection_date=data.get("collection_date") or date.today(),
            session=data["session"],
            accepted_litres=data["accepted_litres"],
            rejected_litres=data.get("rejected_litres") or Decimal("0.00"),
            rejection_reason=(data.get("rejection_reason") or "").strip(),
            recorded_by=request.user,
        )
        try:
            intake.full_clean()
            intake.save()
        except IntegrityError:
            return Response(
                {"detail": ["An entry already exists for this farmer, date and session."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            MilkIntakeSerializer(intake).data, status=status.HTTP_201_CREATED
        )


class FarmerSummary(APIView):
    """GET /api/v1/farmers/<member_no>/summary/ - litres, debt & net balance."""

    permission_classes = [IsCoolerMember]

    def get(self, request, member_no):
        cooler = _require_cooler(request)
        farmer = get_object_or_404(
            Farmer, cooler=cooler, member_number=member_no, is_active=True
        )
        today = date.today()
        summary = services.farmer_summary(farmer, today.year, today.month)
        return Response(FarmerSummarySerializer(summary).data)


class StkPush(APIView):
    """POST /api/v1/payments/stk-push/ - initiate Daraja STK Push (Farmer, Clerk)."""

    permission_classes = [HasRole]
    allowed_roles = FARMER_CLERK

    def post(self, request):
        cooler = _require_cooler(request)
        serializer = STKPushSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        farmer = None
        member_number = data.get("member_number") or ""
        if member_number:
            farmer = get_object_or_404(
                Farmer, cooler=cooler, member_number=member_number, is_active=True
            )
        elif request.role == Membership.Role.FARMER:
            farmer = getattr(request.user, "farmer_profile", None)

        if request.role == Membership.Role.FARMER and farmer is None:
            return Response(
                {"detail": "Your account is not linked to a farmer profile."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            txn = _initiate_stk(
                cooler=cooler,
                farmer=farmer,
                phone=data["phone"],
                amount=data["amount"],
                purpose=MpesaTransaction.Purpose.STORE_DEBT,
                user=request.user,
            )
        except DarajaError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY
            )
        return Response(
            {
                "checkout_request_id": txn.checkout_request_id,
                "merchant_request_id": txn.merchant_request_id,
                "status": txn.status,
                "amount_kes": str(txn.amount_kes),
                "account_reference": txn.account_reference,
            },
            status=status.HTTP_201_CREATED,
        )


class PaymentStatus(APIView):
    """GET /api/v1/payments/status/<checkout_id>/ - poll until the callback lands."""

    permission_classes = [IsCoolerMember]

    def get(self, request, checkout_id):
        cooler = _require_cooler(request)
        txn = get_object_or_404(
            MpesaTransaction, checkout_request_id=checkout_id, cooler=cooler
        )

        if txn.status == MpesaTransaction.Status.PENDING:
            try:
                result = DarajaClient().query_stk_status(txn.checkout_request_id)
            except DarajaError:
                result = None
            if result and result.get("ResultCode") is not None:
                code = int(result["ResultCode"])
                txn.result_code = code
                txn.result_desc = result.get("ResultDesc", "")
                txn.status = (
                    MpesaTransaction.Status.SUCCESS
                    if code == 0
                    else MpesaTransaction.Status.FAILED
                )
                txn.save(
                    update_fields=["result_code", "result_desc", "status", "updated_at"]
                )

        return Response(PaymentStatusSerializer(txn).data)
