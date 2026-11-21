# Django admin registrations for the whole MaziwaLog ledger.

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    Cooler,
    DailyMilkRate,
    Farmer,
    Membership,
    MilkIntake,
    MpesaTransaction,
    MonthlyStatement,
    StoreCreditTransaction,
    User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("email",)
    list_display = ("email", "full_name", "is_active", "is_staff", "date_joined")
    search_fields = ("email", "full_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("full_name", "phone_number")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name", "password1", "password2", "is_staff"),
            },
        ),
    )


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


@admin.register(Cooler)
class CoolerAdmin(admin.ModelAdmin):
    list_display = ("name", "county", "till_or_paybill", "is_subscription_active", "created_at")
    search_fields = ("name", "county")
    list_filter = ("is_subscription_active", "county")
    inlines = [MembershipInline]


@admin.register(DailyMilkRate)
class DailyMilkRateAdmin(admin.ModelAdmin):
    list_display = ("cooler", "effective_date", "rate_per_litre_kes")
    list_filter = ("cooler",)


@admin.register(Farmer)
class FarmerAdmin(admin.ModelAdmin):
    list_display = ("member_number", "full_name", "phone_number", "cooler", "is_active")
    list_filter = ("cooler", "is_active")
    search_fields = ("member_number", "full_name", "phone_number")


@admin.register(MilkIntake)
class MilkIntakeAdmin(admin.ModelAdmin):
    list_display = (
        "collection_date",
        "session",
        "farmer",
        "accepted_litres",
        "rejected_litres",
        "cooler",
    )
    list_filter = ("cooler", "session", "collection_date")
    search_fields = ("farmer__member_number", "farmer__full_name")


@admin.register(StoreCreditTransaction)
class StoreCreditTransactionAdmin(admin.ModelAdmin):
    list_display = ("transaction_date", "farmer", "item_name", "total_amount_kes", "cooler")
    list_filter = ("cooler",)


@admin.register(MonthlyStatement)
class MonthlyStatementAdmin(admin.ModelAdmin):
    list_display = (
        "farmer",
        "year",
        "month",
        "gross_earnings_kes",
        "store_credit_deducted_kes",
        "net_payout_kes",
        "payout_status",
    )
    list_filter = ("cooler", "payout_status", "year")
    search_fields = ("farmer__member_number", "farmer__full_name", "coop_mpesa_payout_receipt")


@admin.register(MpesaTransaction)
class MpesaTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "account_reference",
        "amount_kes",
        "purpose",
        "status",
        "mpesa_receipt_number",
    )
    list_filter = ("cooler", "purpose", "status")
    search_fields = ("checkout_request_id", "mpesa_receipt_number", "account_reference")
    readonly_fields = [f.name for f in MpesaTransaction._meta.fields]
