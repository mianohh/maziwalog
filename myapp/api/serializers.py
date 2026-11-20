"""API serializers."""

from decimal import Decimal

from rest_framework import serializers

from ..models import Farmer, MilkIntake, MpesaTransaction, normalize_msisdn
from ..forms import IntakeForm


class FarmerSerializer(serializers.ModelSerializer):
    cooler_name = serializers.CharField(source="cooler.name", read_only=True)

    class Meta:
        model = Farmer
        fields = [
            "id",
            "member_number",
            "full_name",
            "phone_number",
            "national_id",
            "is_active",
            "cooler_name",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class MilkIntakeSerializer(serializers.ModelSerializer):
    member_number = serializers.CharField(source="farmer.member_number", read_only=True)
    farmer_name = serializers.CharField(source="farmer.full_name", read_only=True)
    recorded_by = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = MilkIntake
        fields = [
            "id",
            "member_number",
            "farmer_name",
            "collection_date",
            "session",
            "accepted_litres",
            "rejected_litres",
            "rejection_reason",
            "recorded_by",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "recorded_by"]


class IntakeCreateSerializer(serializers.Serializer):
    """
    POST /api/v1/intake/ payload:

        member_number, session, accepted_litres, rejected_litres
    """

    member_number = serializers.CharField(max_length=30)
    collection_date = serializers.DateField(required=False)
    session = serializers.ChoiceField(choices=MilkIntake.Session.choices)
    accepted_litres = serializers.DecimalField(max_digits=8, decimal_places=2)
    rejected_litres = serializers.DecimalField(
        max_digits=8, decimal_places=2, required=False, default=Decimal("0.00")
    )
    rejection_reason = serializers.CharField(max_length=150, required=False, allow_blank=True)


class FarmerSummarySerializer(serializers.Serializer):
    """Response of GET /api/v1/farmers/<member_no>/summary/."""

    member_number = serializers.CharField()
    full_name = serializers.CharField()
    year = serializers.IntegerField()
    month = serializers.IntegerField()
    accepted_litres = serializers.DecimalField(max_digits=10, decimal_places=2)
    rate_per_litre_kes = serializers.DecimalField(max_digits=10, decimal_places=2)
    gross_kes = serializers.DecimalField(max_digits=12, decimal_places=2)
    store_credit_kes = serializers.DecimalField(max_digits=12, decimal_places=2)
    debt_repaid_kes = serializers.DecimalField(max_digits=12, decimal_places=2)
    unpaid_credit_kes = serializers.DecimalField(max_digits=12, decimal_places=2)
    net_kes = serializers.DecimalField(max_digits=12, decimal_places=2)


class STKPushSerializer(serializers.Serializer):
    """POST /api/v1/payments/stk-push/ payload."""

    member_number = serializers.CharField(max_length=30, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=15)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=1)

    def validate_phone(self, value):
        msisdn = normalize_msisdn(value)
        if len(msisdn) != 12 or not msisdn.startswith("254"):
            raise serializers.ValidationError("Enter a valid Kenyan number, e.g. 0712345678.")
        return msisdn

    def validate_amount(self, value):
        if value != value.to_integral_value():
            raise serializers.ValidationError("Daraja requires a whole-shilling amount.")
        return value


class PaymentStatusSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = MpesaTransaction
        fields = [
            "checkout_request_id",
            "status",
            "status_display",
            "amount_kes",
            "mpesa_receipt_number",
            "result_code",
            "result_desc",
            "account_reference",
            "updated_at",
        ]
