"""Tenant-aware forms for intake, credit, payments, and registration."""

import re

from django import forms
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from .models import (
    Cooler,
    DailyMilkRate,
    Farmer,
    Membership,
    MilkIntake,
    MpesaTransaction,
    StoreCreditTransaction,
    normalize_msisdn,
)


class MembershipSetupForm(forms.Form):
    """Account type and cooler selection shared by signup and completion."""

    account_type = forms.ChoiceField(
        label="Account type",
        choices=[("CLERK", "Clerk"), ("FARMER", "Farmer")],
        widget=forms.RadioSelect,
    )
    cooler = forms.ModelChoiceField(
        label="Cooler location",
        queryset=Cooler.objects.filter(is_subscription_active=True).order_by("name"),
        empty_label="Select a cooler",
    )
    phone_number = forms.CharField(
        required=False,
        max_length=20,
        label="Phone number",
        widget=forms.TextInput(attrs={"placeholder": "0712345678"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.RadioSelect):
                widget.attrs.setdefault("class", "form-check-input")
            elif not isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-control")

    def clean_account_type(self):
        value = self.cleaned_data["account_type"]
        if value not in {"CLERK", "FARMER"}:
            raise ValidationError("Choose Clerk or Farmer.")
        return value

    def clean_phone_number(self):
        raw = (self.cleaned_data.get("phone_number") or "").strip()
        if not raw:
            return ""
        return normalize_msisdn(raw)

    def clean(self):
        cleaned = super().clean()
        account_type = cleaned.get("account_type")
        phone = cleaned.get("phone_number") or ""
        if account_type == "FARMER" and not phone:
            self.add_error("phone_number", "A phone number is required for farmers.")
        cooler = cleaned.get("cooler")
        if self.user is not None and cooler is not None:
            if Membership.objects.filter(user=self.user, cooler=cooler).exists():
                self.add_error(
                    "cooler",
                    "You already belong to this cooler workspace.",
                )
        return cleaned

    @staticmethod
    def _next_member_number(cooler):
        pattern = re.compile(rf"^{re.escape(cooler.account_prefix)}-(\d+)$")
        highest = 0
        for number in Farmer.objects.filter(cooler=cooler).values_list(
            "member_number", flat=True
        ):
            match = pattern.match(number or "")
            if match:
                highest = max(highest, int(match.group(1)))
        return f"{cooler.account_prefix}-{highest + 1:04d}"

    def attach(self, user, request=None):
        """Create the membership and, for farmers, the linked profile."""
        account_type = self.cleaned_data["account_type"]
        cooler = self.cleaned_data["cooler"]
        try:
            with transaction.atomic():
                if Membership.objects.filter(user=user, cooler=cooler).exists():
                    return
                membership = Membership.objects.create(
                    user=user,
                    cooler=cooler,
                    role=account_type,
                )
                if account_type == "FARMER" and not hasattr(user, "farmer_profile"):
                    phone = self.cleaned_data.get("phone_number") or ""
                    Farmer.objects.create(
                        cooler=cooler,
                        user=user,
                        member_number=self._next_member_number(cooler),
                        full_name=user.full_name or user.email,
                        phone_number=phone,
                    )
                    if phone:
                        user.phone_number = phone
                        user.save(update_fields=["phone_number"])
        except IntegrityError as exc:
            raise ValidationError(
                "This registration could not be completed. Choose a different cooler."
            ) from exc
        return membership


class MaziwaSignupForm(MembershipSetupForm):
    """Email signup form; allauth adds email/password on top of these fields."""

    full_name = forms.CharField(
        max_length=150,
        label="Full name",
        widget=forms.TextInput(attrs={"placeholder": "Jane Wanjiku"}),
    )

    def __init__(self, *args, **kwargs):
        self.field_order = [
            "email",
            "password1",
            "password2",
            "full_name",
            "account_type",
            "cooler",
            "phone_number",
        ]
        super().__init__(*args, **kwargs)

    def signup(self, request, user):
        user.full_name = (self.cleaned_data.get("full_name") or "").strip()
        user.save(update_fields=["full_name"])
        self.attach(user, request)


class FarmerForm(forms.ModelForm):
    class Meta:
        model = Farmer
        fields = [
            "member_number",
            "full_name",
            "phone_number",
            "national_id",
            "is_active",
        ]
        widgets = {
            "member_number": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g. MZ-0042"}
            ),
            "full_name": forms.TextInput(attrs={"class": "form-control"}),
            "phone_number": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "0712345678 or 254712345678"}
            ),
            "national_id": forms.TextInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, *args, cooler=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cooler = cooler
        self.fields["member_number"].required = True
        self.fields["full_name"].required = True
        self.fields["phone_number"].required = True

    def clean_phone_number(self):
        return normalize_msisdn(self.cleaned_data["phone_number"])

    def clean(self):
        cleaned = super().clean()
        member = cleaned.get("member_number")
        if member and self.cooler is not None:
            qs = Farmer.objects.filter(cooler=self.cooler, member_number=member)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(
                    {"member_number": "This member number is already registered in this cooler."}
                )
        return cleaned


class IntakeForm(forms.ModelForm):
    REJECTION_REASONS = [
        ("", "---------"),
        ("High Acidity", "High Acidity"),
        ("Water Adulteration", "Water Adulteration"),
        ("Sour/Curdled", "Sour/Curdled"),
        ("Foreign Matter", "Foreign Matter"),
    ]

    class Meta:
        model = MilkIntake
        fields = [
            "farmer",
            "collection_date",
            "session",
            "accepted_litres",
            "rejected_litres",
            "rejection_reason",
        ]
        widgets = {
            "farmer": forms.Select(attrs={"class": "form-select"}),
            "collection_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "session": forms.Select(attrs={"class": "form-select"}),
            "accepted_litres": forms.NumberInput(
                attrs={
                    "class": "form-control ml-intake-field",
                    "step": "0.01",
                    "min": "0",
                    "placeholder": "0.00",
                }
            ),
            "rejected_litres": forms.NumberInput(
                attrs={
                    "class": "form-control ml-intake-field",
                    "step": "0.01",
                    "min": "0",
                    "placeholder": "0.00",
                }
            ),
            "rejection_reason": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, cooler=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cooler = cooler
        self.fields["rejection_reason"] = forms.ChoiceField(
            choices=self.REJECTION_REASONS,
            required=False,
            label="Rejection reason",
            widget=forms.Select(attrs={"class": "form-select"}),
        )
        if cooler is not None:
            self.fields["farmer"].queryset = Farmer.objects.filter(
                cooler=cooler, is_active=True
            ).order_by("full_name")
        self.fields["rejected_litres"].initial = "0.00"
        self.fields["rejection_reason"].required = False

    def clean(self):
        cleaned = super().clean()
        accepted = cleaned.get("accepted_litres")
        rejected = cleaned.get("rejected_litres") or 0
        reason = (cleaned.get("rejection_reason") or "").strip()

        if accepted is None:
            raise ValidationError("Accepted litres are required.")
        if accepted < 0 or rejected < 0:
            raise ValidationError("Litres cannot be negative.")
        if rejected > 0 and not reason:
            self.add_error(
                "rejection_reason",
                "A rejection reason is mandatory when litres are rejected.",
            )
        if accepted == 0 and rejected == 0:
            raise ValidationError("Record at least one accepted or rejected litre.")

        farmer = cleaned.get("farmer")
        if farmer is not None and self.cooler is not None and farmer.cooler_id != self.cooler.id:
            raise ValidationError("That farmer belongs to a different cooler workspace.")
        return cleaned


class StoreCreditForm(forms.ModelForm):
    class Meta:
        model = StoreCreditTransaction
        fields = ["farmer", "item_name", "quantity", "unit_price_kes", "transaction_date"]
        widgets = {
            "farmer": forms.Select(attrs={"class": "form-select"}),
            "item_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Dairy meal 70kg / Mineral salts ..."}
            ),
            "quantity": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0.01"}
            ),
            "unit_price_kes": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0"}
            ),
            "transaction_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
        }

    def __init__(self, *args, cooler=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cooler = cooler
        if cooler is not None:
            self.fields["farmer"].queryset = Farmer.objects.filter(
                cooler=cooler, is_active=True
            ).order_by("full_name")

    def clean(self):
        cleaned = super().clean()
        farmer = cleaned.get("farmer")
        if farmer is not None and self.cooler is not None and farmer.cooler_id != self.cooler.id:
            raise ValidationError("That farmer belongs to a different cooler workspace.")
        return cleaned


class DailyMilkRateForm(forms.ModelForm):
    class Meta:
        model = DailyMilkRate
        fields = ["effective_date", "rate_per_litre_kes"]
        widgets = {
            "effective_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "rate_per_litre_kes": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0"}
            ),
        }


class PayoutReceiptForm(forms.Form):
    """Manager records the co-op's own M-Pesa payout receipt (non-custodial)."""

    receipt = forms.CharField(
        max_length=30,
        label="M-Pesa receipt code",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "SFF7XK21LM"}
        ),
    )

    def clean_receipt(self):
        receipt = (self.cleaned_data["receipt"] or "").strip().upper()
        if not receipt:
            raise ValidationError("Receipt code is required.")
        return receipt


class STKPushForm(forms.Form):
    """Farmer / clerk initiated store-debt STK Push."""

    phone_number = forms.CharField(
        max_length=15,
        label="M-Pesa phone number",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "0712345678"}
        ),
    )
    amount_kes = forms.DecimalField(
        max_value=70000,
        min_value=1,
        max_digits=10,
        decimal_places=2,
        label="Amount (KES)",
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "1", "min": "1"}
        ),
    )

    def clean_phone_number(self):
        msisdn = normalize_msisdn(self.cleaned_data["phone_number"])
        if len(msisdn) != 12 or not msisdn.startswith("254"):
            raise ValidationError("Enter a valid Kenyan number, e.g. 0712345678.")
        return msisdn

    def clean_amount_kes(self):
        amount = self.cleaned_data["amount_kes"]
        if amount != amount.to_integral_value():
            raise ValidationError("Daraja requires a whole-shilling amount.")
        return amount
