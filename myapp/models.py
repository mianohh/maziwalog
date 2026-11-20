"""
MaziwaLog domain models.

All monetary amounts (KES) and milk volumes (litres) use DecimalField to
eliminate floating-point rounding drift during monthly co-op reconciliations.
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Custom user & cooler role membership
# ---------------------------------------------------------------------------


class UserManager(BaseUserManager):
    """Email is the only login credential."""

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email address is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=15, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    objects = UserManager()

    def __str__(self):
        return self.email

    @property
    def display_name(self):
        return self.full_name or self.email.split("@")[0]


class Membership(models.Model):
    """A user belongs to one or more coolers with a role."""

    class Role(models.TextChoices):
        MANAGER = "MANAGER", "Cooler Manager"
        CLERK = "CLERK", "Intake Clerk"
        FARMER = "FARMER", "Dairy Farmer"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    cooler = models.ForeignKey("Cooler", on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CLERK)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "cooler")

    def __str__(self):
        return f"{self.user.email} - {self.cooler.name} ({self.role})"


# ---------------------------------------------------------------------------
# Multi-tenant cooler & milk rate models
# ---------------------------------------------------------------------------


class Cooler(models.Model):
    """An independent dairy cooler / milk collection centre (tenant)."""

    name = models.CharField(max_length=150)
    county = models.CharField(max_length=80)
    till_or_paybill = models.CharField(
        max_length=20, help_text="Co-op M-Pesa Till or Paybill"
    )
    account_prefix = models.CharField(max_length=10, default="MZ")
    is_subscription_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.county})"

    @property
    def slug(self):
        return f"{self.account_prefix}-{self.pk:04d}"


class DailyMilkRate(models.Model):
    """Rate applied per accepted litre; newest effective_date wins."""

    cooler = models.ForeignKey(Cooler, on_delete=models.CASCADE, related_name="rates")
    effective_date = models.DateField(default=timezone.localdate)
    rate_per_litre_kes = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("50.00")
    )

    class Meta:
        unique_together = ("cooler", "effective_date")
        ordering = ["-effective_date"]

    def __str__(self):
        return f"{self.cooler.name} @ KES {self.rate_per_litre_kes}/L from {self.effective_date}"

    @classmethod
    def rate_for(cls, cooler, on_date=None):
        """Return the rate in force on ``on_date`` (default: today)."""
        on_date = on_date or timezone.localdate()
        rate = (
            cls.objects.filter(cooler=cooler, effective_date__lte=on_date).order_by(
                "-effective_date"
            ).first()
        )
        if rate is None:
            rate = cls.objects.filter(cooler=cooler).order_by("-effective_date").first()
        return rate.rate_per_litre_kes if rate else Decimal("50.00")


# ---------------------------------------------------------------------------
# Farmers, intake, store credit & monthly statements
# ---------------------------------------------------------------------------


class Farmer(models.Model):
    cooler = models.ForeignKey(Cooler, on_delete=models.CASCADE, related_name="farmers")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="farmer_profile",
    )
    member_number = models.CharField(max_length=30)
    full_name = models.CharField(max_length=150)
    phone_number = models.CharField(
        max_length=15, help_text="Format: 2547XXXXXXXX"
    )
    national_id = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("cooler", "member_number")
        indexes = [models.Index(fields=["cooler", "member_number"])]
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.member_number} - {self.full_name}"

    def clean(self):
        """Normalise Kenyan MSISDNs to 2547XXXXXXXX / 2541XXXXXXXX."""
        super().clean()
        self.phone_number = normalize_msisdn(self.phone_number)

    def save(self, *args, **kwargs):
        self.phone_number = normalize_msisdn(self.phone_number)
        super().save(*args, **kwargs)


class MilkIntake(models.Model):
    """A single AM or PM delivery by one farmer on one date."""

    class Session(models.TextChoices):
        MORNING = "MORNING", "Morning"
        EVENING = "EVENING", "Evening"

    cooler = models.ForeignKey(Cooler, on_delete=models.CASCADE, related_name="intakes")
    farmer = models.ForeignKey(
        Farmer, on_delete=models.PROTECT, related_name="intakes"
    )
    collection_date = models.DateField(default=timezone.localdate)
    session = models.CharField(max_length=10, choices=Session.choices)
    accepted_litres = models.DecimalField(max_digits=8, decimal_places=2)
    rejected_litres = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("0.00")
    )
    rejection_reason = models.CharField(max_length=150, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="intakes_recorded"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("farmer", "collection_date", "session")
        ordering = ["-collection_date", "-created_at"]

    def __str__(self):
        return (
            f"{self.farmer.member_number} {self.collection_date} "
            f"{self.session}: {self.accepted_litres}L"
        )

    def clean(self):
        super().clean()
        if self.rejected_litres and self.rejected_litres > 0 and not self.rejection_reason:
            raise ValidationError(
                {"rejection_reason": "A rejection reason is mandatory when litres are rejected."}
            )
        if self.accepted_litres is not None and self.accepted_litres < 0:
            raise ValidationError({"accepted_litres": "Accepted litres cannot be negative."})


class StoreCreditTransaction(models.Model):
    """Agrovet / dairy-meal credit purchase against future milk earnings."""

    cooler = models.ForeignKey(
        Cooler, on_delete=models.CASCADE, related_name="credit_sales"
    )
    farmer = models.ForeignKey(
        Farmer, on_delete=models.PROTECT, related_name="credit_sales"
    )
    item_name = models.CharField(max_length=120)
    quantity = models.DecimalField(max_digits=8, decimal_places=2)
    unit_price_kes = models.DecimalField(max_digits=10, decimal_places=2)
    total_amount_kes = models.DecimalField(max_digits=10, decimal_places=2)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="credit_sales_recorded",
    )
    transaction_date = models.DateField(default=timezone.localdate)

    class Meta:
        ordering = ["-transaction_date", "-id"]

    def __str__(self):
        return f"{self.farmer.member_number}: {self.item_name} KES {self.total_amount_kes}"

    def save(self, *args, **kwargs):
        self.total_amount_kes = self.quantity * self.unit_price_kes
        super().save(*args, **kwargs)


class MonthlyStatement(models.Model):
    """Month-end payout reconciliation row for one farmer."""

    class PayoutStatus(models.TextChoices):
        PENDING = "PENDING", "Pending Payout"
        PAID = "PAID", "Paid via Co-op Till"
        DEBT_CARRYOVER = "DEBT_CARRYOVER", "Store Debt Exceeds Milk"

    cooler = models.ForeignKey(
        Cooler, on_delete=models.CASCADE, related_name="statements"
    )
    farmer = models.ForeignKey(
        Farmer, on_delete=models.PROTECT, related_name="statements"
    )
    year = models.PositiveIntegerField()
    month = models.PositiveSmallIntegerField()
    total_accepted_litres = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00")
    )
    applied_rate_kes = models.DecimalField(max_digits=10, decimal_places=2)
    gross_earnings_kes = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    store_credit_deducted_kes = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    net_payout_kes = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    payout_status = models.CharField(
        max_length=20, choices=PayoutStatus.choices, default=PayoutStatus.PENDING
    )
    coop_mpesa_payout_receipt = models.CharField(
        max_length=30, blank=True, null=True, unique=True
    )
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("farmer", "year", "month")
        ordering = ["-year", "-month"]

    def __str__(self):
        return f"{self.farmer.member_number} {self.year}-{self.month:02d} KES {self.net_payout_kes}"

    @property
    def month_name(self):
        return timezone.datetime(self.year, self.month, 1).strftime("%B %Y")


# ---------------------------------------------------------------------------
# Idempotent M-Pesa transaction model
# ---------------------------------------------------------------------------


class MpesaTransaction(models.Model):
    class Purpose(models.TextChoices):
        STORE_DEBT = "STORE_DEBT", "Store Debt Repayment"
        SUBSCRIPTION = "SUBSCRIPTION", "Cooler SaaS Subscription"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending PIN"
        SUCCESS = "SUCCESS", "Completed"
        FAILED = "FAILED", "Failed / Cancelled"

    cooler = models.ForeignKey(
        Cooler, on_delete=models.CASCADE, related_name="mpesa_txns"
    )
    farmer = models.ForeignKey(
        Farmer, on_delete=models.SET_NULL, null=True, blank=True, related_name="mpesa_txns"
    )
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    amount_kes = models.DecimalField(max_digits=10, decimal_places=2)
    phone_number = models.CharField(max_length=15)
    account_reference = models.CharField(max_length=30)
    merchant_request_id = models.CharField(max_length=100, db_index=True, blank=True, default="")
    checkout_request_id = models.CharField(max_length=100, unique=True)
    mpesa_receipt_number = models.CharField(
        max_length=30, unique=True, null=True, blank=True
    )
    result_code = models.IntegerField(null=True, blank=True)
    result_desc = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.PENDING
    )
    raw_callback_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.account_reference} KES {self.amount_kes} [{self.status}]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize_msisdn(raw: str) -> str:
    """Normalise Kenyan phone numbers to the 2547XXXXXXXX / 2541XXXXXXXX form."""
    if not raw:
        return raw
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("7") and len(digits) == 9:
        digits = "254" + digits
    elif digits.startswith("1") and len(digits) == 9:
        digits = "254" + digits
    elif digits.startswith("254") and len(digits) == 12:
        pass
    elif digits.startswith("54") and len(digits) == 12:  # +254 mistakenly split
        digits = "254" + digits[2:]
    return digits
