from datetime import date, datetime
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import (
    DailyMilkRate,
    Farmer,
    MilkIntake,
    MpesaTransaction,
    MonthlyStatement,
    StoreCreditTransaction,
)

ZERO = Decimal("0.00")


def month_bounds(year: int, month: int):
    """
    Half-open UTC datetime range [start, end) covering one local calendar month.

    Used instead of ``created_at__year`` / ``created_at__month``: those compile to
    CONVERT_TZ(..., 'UTC', 'Africa/Nairobi'), which returns NULL on MariaDB/MySQL
    servers whose ``mysql.time_zone_name`` table has never been populated.
    """
    start = timezone.make_aware(datetime(year, month, 1))
    if month == 12:
        end = timezone.make_aware(datetime(year + 1, 1, 1))
    else:
        end = timezone.make_aware(datetime(year, month + 1, 1))
    return start, end


def generate_monthly_statement(farmer: Farmer, year: int, month: int) -> MonthlyStatement:
    """Compute (or recompute) one farmer's statement for YYYY-MM."""
    accepted_litres = (
        MilkIntake.objects.filter(
            cooler=farmer.cooler,
            farmer=farmer,
            collection_date__year=year,
            collection_date__month=month,
        ).aggregate(total=Sum("accepted_litres"))["total"]
        or ZERO
    )

    rate = DailyMilkRate.rate_for(farmer.cooler, date(year, month, 1))
    gross_earnings = accepted_litres * rate

    total_credit = (
        StoreCreditTransaction.objects.filter(
            cooler=farmer.cooler,
            farmer=farmer,
            transaction_date__year=year,
            transaction_date__month=month,
        ).aggregate(total=Sum("total_amount_kes"))["total"]
        or ZERO
    )

    start, end = month_bounds(year, month)
    debt_repaid = (
        MpesaTransaction.objects.filter(
            cooler=farmer.cooler,
            farmer=farmer,
            purpose=MpesaTransaction.Purpose.STORE_DEBT,
            status=MpesaTransaction.Status.SUCCESS,
            created_at__gte=start,
            created_at__lt=end,
        ).aggregate(total=Sum("amount_kes"))["total"]
        or ZERO
    )

    unpaid_credit = max(ZERO, total_credit - debt_repaid)
    net_payout = gross_earnings - unpaid_credit

    status = (
        MonthlyStatement.PayoutStatus.DEBT_CARRYOVER
        if net_payout < ZERO
        else MonthlyStatement.PayoutStatus.PENDING
    )

    statement, _ = MonthlyStatement.objects.update_or_create(
        farmer=farmer,
        year=year,
        month=month,
        defaults={
            "cooler": farmer.cooler,
            "total_accepted_litres": accepted_litres,
            "applied_rate_kes": rate,
            "gross_earnings_kes": gross_earnings,
            "store_credit_deducted_kes": unpaid_credit,
            "net_payout_kes": net_payout,
            "payout_status": status,
        },
    )
    return statement


def generate_cooler_statements(cooler, year: int, month: int) -> int:
    """Run the engine for every active farmer of one cooler (tenant-scoped)."""
    count = 0
    for farmer in Farmer.objects.filter(cooler=cooler, is_active=True):
        generate_monthly_statement(farmer, year, month)
        count += 1
    return count


def _month_totals(cooler, year: int, month: int):
    """
    Per-farmer month totals as three grouped aggregates.

    Returns (rate, accepted, credit, repaid) — the last three keyed by farmer_id.
    """
    rate = DailyMilkRate.rate_for(cooler, date(year, month, 1))

    def totals(queryset, field):
        return {
            row["farmer_id"]: row["total"] or ZERO
            for row in queryset.values("farmer_id").annotate(total=Sum(field))
        }

    accepted = totals(
        MilkIntake.objects.filter(
            cooler=cooler, collection_date__year=year, collection_date__month=month
        ),
        "accepted_litres",
    )
    credit = totals(
        StoreCreditTransaction.objects.filter(
            cooler=cooler, transaction_date__year=year, transaction_date__month=month
        ),
        "total_amount_kes",
    )
    start, end = month_bounds(year, month)
    repaid = totals(
        MpesaTransaction.objects.filter(
            cooler=cooler,
            purpose=MpesaTransaction.Purpose.STORE_DEBT,
            status=MpesaTransaction.Status.SUCCESS,
            created_at__gte=start,
            created_at__lt=end,
        ),
        "amount_kes",
    )
    return rate, accepted, credit, repaid


def _summary_row(farmer, year: int, month: int, rate, accepted, credit, repaid) -> dict:
    a = accepted.get(farmer.pk, ZERO)
    c = credit.get(farmer.pk, ZERO)
    r = repaid.get(farmer.pk, ZERO)
    unpaid = max(ZERO, c - r)
    gross = a * rate
    return {
        "member_number": farmer.member_number,
        "full_name": farmer.full_name,
        "year": year,
        "month": month,
        "accepted_litres": a,
        "rate_per_litre_kes": rate,
        "gross_kes": gross,
        "store_credit_kes": c,
        "debt_repaid_kes": r,
        "unpaid_credit_kes": unpaid,
        "net_kes": gross - unpaid,
    }


def farmer_summary(farmer: Farmer, year: int, month: int) -> dict:
    """Current-month position used by the farmer portal and GET summary API."""
    totals = _month_totals(farmer.cooler, year, month)
    return _summary_row(farmer, year, month, *totals)


def farmer_summaries(cooler, year: int, month: int) -> list:
    """Current-month position for every active farmer — no per-farmer query loop."""
    rate, accepted, credit, repaid = _month_totals(cooler, year, month)
    return [
        _summary_row(farmer, year, month, rate, accepted, credit, repaid)
        for farmer in Farmer.objects.filter(cooler=cooler, is_active=True)
    ]


def cooler_dashboard_stats(cooler) -> dict:
    """Roll-up numbers for the manager home screen (all queries tenant-scoped)."""
    today = date.today()
    month_start = today.replace(day=1)

    litres_today = (
        MilkIntake.objects.filter(cooler=cooler, collection_date=today).aggregate(
            total=Sum("accepted_litres")
        )["total"]
        or ZERO
    )
    litres_month = (
        MilkIntake.objects.filter(
            cooler=cooler, collection_date__gte=month_start
        ).aggregate(total=Sum("accepted_litres"))["total"]
        or ZERO
    )
    credit_month = (
        StoreCreditTransaction.objects.filter(
            cooler=cooler, transaction_date__gte=month_start
        ).aggregate(total=Sum("total_amount_kes"))["total"]
        or ZERO
    )
    summaries = farmer_summaries(cooler, today.year, today.month)
    credit_outstanding = sum((s["unpaid_credit_kes"] for s in summaries), ZERO)

    return {
        "farmers": len(summaries),
        "litres_today": litres_today,
        "litres_month": litres_month,
        "credit_month": credit_month,
        "credit_outstanding": credit_outstanding,
        "rate": DailyMilkRate.rate_for(cooler),
        "pending_statements": MonthlyStatement.objects.filter(
            cooler=cooler, payout_status=MonthlyStatement.PayoutStatus.PENDING
        ).count(),
        "pending_payments": MpesaTransaction.objects.filter(
            cooler=cooler, status=MpesaTransaction.Status.PENDING
        ).count(),
    }
