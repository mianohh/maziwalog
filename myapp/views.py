# MaziwaLog web views.

# Tenant isolation: every query is filtered by
# ``request.cooler`` and every object lookup is scoped to that cooler, so a
# cross-workspace id resolves to HTTP 404.


import calendar
import json
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services
from .daraja import DarajaClient, DarajaError
from .forms import (
    DailyMilkRateForm,
    FarmerForm,
    IntakeForm,
    MembershipSetupForm,
    PayoutReceiptForm,
    STKPushForm,
    StoreCreditForm,
)
from .middleware import STAFF, SESSION_KEY
from .models import (
    DailyMilkRate,
    Farmer,
    Membership,
    MilkIntake,
    MpesaTransaction,
    MPESA_ACCOUNT_REFERENCE,
    MonthlyStatement,
    StoreCreditTransaction,
)


# ---------------------------------------------------------------------------
# Helpers & decorators
# ---------------------------------------------------------------------------


def _require_cooler(request):
    """Return the active tenant or raise 404."""
    if request.cooler is None:
        raise Http404("No cooler workspace is active for this account.")
    return request.cooler


def role_required(*roles):
    """Restrict a view to the given cooler roles (staff always passes)."""

    def decorator(view_func):
        @login_required
        def wrapper(request, *args, **kwargs):
            _require_cooler(request)
            if request.role not in roles and request.role != STAFF:
                messages.error(request, "Your role does not allow that action.")
                return redirect("dashboard")
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Entry points & role dispatch
# ---------------------------------------------------------------------------


def home(request):
    """Public landing page; signed-in users get an 'Open dashboard' CTA."""
    return render(request, "landing.html")


@login_required
def dashboard(request):
    """Send each role to its workspace."""
    if (
        not request.user.is_staff
        and not Membership.objects.filter(user=request.user).exists()
    ):
        return redirect("complete_registration")
    if request.role == Membership.Role.FARMER:
        return redirect("farmer_portal")
    if request.role == Membership.Role.CLERK:
        return redirect("clerk_intake")
    if request.cooler is None:
        messages.warning(
            request,
            "Your account is not linked to a cooler yet. Ask a manager to add you.",
        )
        return redirect("admin:index") if request.user.is_staff else render(
            request, "dashboard/no_cooler.html", {}
        )
    return redirect("manager_home")


@login_required
def complete_registration(request):
    """Collect account type and cooler after signup (email or Google)."""
    if Membership.objects.filter(user=request.user).exists():
        return redirect("dashboard")
    if request.method == "POST":
        form = MembershipSetupForm(request.POST, user=request.user)
        if form.is_valid():
            form.attach(request.user, request)
            messages.success(request, "Registration complete. Welcome to MaziwaLog.")
            return redirect("dashboard")
    else:
        form = MembershipSetupForm(user=request.user)
    return render(request, "account/complete_registration.html", {"form": form})


@role_required(Membership.Role.MANAGER)
def manager_home(request):
    cooler = request.cooler
    stats = services.cooler_dashboard_stats(cooler)
    today = date.today()

    recent_intakes = (
        MilkIntake.objects.filter(cooler=cooler)
        .select_related("farmer", "recorded_by")[:12]
    )
    pending_statements = (
        MonthlyStatement.objects.filter(
            cooler=cooler, payout_status=MonthlyStatement.PayoutStatus.PENDING
        )
        .select_related("farmer")[:8]
    )
    unpaid_debt = (
        MonthlyStatement.objects.filter(
            cooler=cooler, payout_status=MonthlyStatement.PayoutStatus.DEBT_CARRYOVER
        )
        .select_related("farmer")[:8]
    )

    today_totals = MilkIntake.objects.filter(
        cooler=cooler, collection_date=today
    ).aggregate(accepted=Sum("accepted_litres"), rejected=Sum("rejected_litres"), rows=Count("id"))
    rejected_today = today_totals["rejected"] or Decimal("0.00")
    total_today = (today_totals["accepted"] or Decimal("0")) + rejected_today

    context = {
        "stats": stats,
        "recent_intakes": recent_intakes,
        "pending_statements": pending_statements,
        "unpaid_debt": unpaid_debt,
        "today": today,
        "month_label": today.strftime("%B %Y"),
        "rate_form": DailyMilkRateForm(),
        "rejected_today": rejected_today,
        "rejection_rate_pct": (
            round(rejected_today / total_today * 100, 1)
            if today_totals["rows"] and total_today
            else Decimal("0.0")
        ),
        "pending_payout_kes": (
            MonthlyStatement.objects.filter(
                cooler=cooler, payout_status=MonthlyStatement.PayoutStatus.PENDING
            ).aggregate(total=Sum("net_payout_kes"))["total"]
            or Decimal("0.00")
        ),
        "recent_transactions": MpesaTransaction.objects.filter(cooler=cooler)
        .select_related("farmer")
        .order_by("-created_at")[:8],
    }
    return render(request, "dashboard/manager_home.html", context)


# ---------------------------------------------------------------------------
# Morning & evening milk intake ledger (clerk)
# ---------------------------------------------------------------------------


@role_required(Membership.Role.CLERK, Membership.Role.MANAGER)
def clerk_intake(request):
    cooler = request.cooler
    today = date.today()

    if request.method == "POST":
        form = IntakeForm(request.POST, cooler=cooler)
        if form.is_valid():
            intake = form.save(commit=False)
            intake.cooler = cooler
            intake.recorded_by = request.user
            try:
                intake.save()
            except Exception:  # unique_together race (farmer/date/session)
                form.add_error(
                    None,
                    "That farmer already has a "
                    f"{intake.get_session_display()} entry for {intake.collection_date}.",
                )
            else:
                messages.success(
                    request,
                    f"Recorded {intake.accepted_litres} L from "
                    f"{intake.farmer.full_name} ({intake.get_session_display()}).",
                )
                return redirect("clerk_intake")
    else:
        form = IntakeForm(cooler=cooler)

    day_entries = (
        MilkIntake.objects.filter(cooler=cooler, collection_date=today)
        .select_related("farmer", "recorded_by")
        .order_by("created_at")
    )
    day_totals = day_entries.aggregate(
        accepted=Sum("accepted_litres"), rejected=Sum("rejected_litres")
    )
    month_totals = (
        MilkIntake.objects.filter(
            cooler=cooler, collection_date__year=today.year, collection_date__month=today.month
        ).aggregate(accepted=Sum("accepted_litres"))
    )

    context = {
        "form": form,
        "day_entries": day_entries,
        "day_accepted": day_totals["accepted"] or Decimal("0.00"),
        "day_rejected": day_totals["rejected"] or Decimal("0.00"),
        "month_accepted": month_totals["accepted"] or Decimal("0.00"),
        "today": today,
        "rate": DailyMilkRate.rate_for(cooler),
    }
    return render(request, "dashboard/clerk_intake.html", context)


# ---------------------------------------------------------------------------
# Farmer portal
# ---------------------------------------------------------------------------


@login_required
def farmer_portal(request):
    _require_cooler(request)
    today = date.today()

    farmer = getattr(request.user, "farmer_profile", None)
    if farmer is None or farmer.cooler_id != request.cooler.id:
        if request.role in (Membership.Role.MANAGER, Membership.Role.CLERK, STAFF):
            return redirect("manager_home")
        return render(request, "dashboard/no_cooler.html", {})

    summary = services.farmer_summary(farmer, today.year, today.month)
    intakes = MilkIntake.objects.filter(farmer=farmer)[:15]
    credits = StoreCreditTransaction.objects.filter(farmer=farmer)[:15]
    statements = MonthlyStatement.objects.filter(farmer=farmer)[:12]
    repayments = MpesaTransaction.objects.filter(
        farmer=farmer, purpose=MpesaTransaction.Purpose.STORE_DEBT
    ).order_by("-created_at")[:15]

    context = {
        "farmer": farmer,
        "summary": summary,
        "intakes": intakes,
        "credits": credits,
        "statements": statements,
        "repayments": repayments,
        "month_label": today.strftime("%B %Y"),
        "stk_form": STKPushForm(
            initial={"phone_number": farmer.phone_number, "amount_kes": summary["unpaid_credit_kes"]}
        ),
    }
    return render(request, "dashboard/farmer_portal.html", context)


# ---------------------------------------------------------------------------
# Farmers
# ---------------------------------------------------------------------------


@role_required(Membership.Role.MANAGER, Membership.Role.CLERK)
def farmer_list(request):
    cooler = request.cooler
    farmers = Farmer.objects.filter(cooler=cooler).order_by("full_name")
    return render(request, "farmers/list.html", {"farmers": farmers, "form": FarmerForm()})


@role_required(Membership.Role.MANAGER)
def farmer_create(request):
    cooler = request.cooler
    if request.method == "POST":
        form = FarmerForm(request.POST, cooler=cooler)
        if form.is_valid():
            farmer = form.save(commit=False)
            farmer.cooler = cooler
            farmer.save()
            messages.success(request, f"Farmer {farmer.full_name} registered.")
            return redirect("farmer_detail", pk=farmer.pk)
    else:
        form = FarmerForm(cooler=cooler)
    return render(request, "farmers/form.html", {"form": form, "title": "Register farmer"})


@role_required(Membership.Role.MANAGER, Membership.Role.CLERK)
def farmer_detail(request, pk):
    cooler = _require_cooler(request)
    # Scoped lookup: an id from another cooler returns 404.
    farmer = get_object_or_404(Farmer, pk=pk, cooler=cooler)
    today = date.today()
    summary = services.farmer_summary(farmer, today.year, today.month)
    intakes = MilkIntake.objects.filter(farmer=farmer)[:20]
    credits = StoreCreditTransaction.objects.filter(farmer=farmer)[:20]
    statements = MonthlyStatement.objects.filter(farmer=farmer)[:12]
    return render(
        request,
        "farmers/farmer_detail.html",
        {
            "farmer": farmer,
            "summary": summary,
            "intakes": intakes,
            "credits": credits,
            "statements": statements,
            "month_label": today.strftime("%B %Y"),
        },
    )


# ---------------------------------------------------------------------------
# Agrovet / dairy-meal store credit book
# ---------------------------------------------------------------------------


@role_required(Membership.Role.MANAGER, Membership.Role.CLERK)
def store_credit_list(request):
    cooler = _require_cooler(request)
    rows = (
        StoreCreditTransaction.objects.filter(cooler=cooler)
        .select_related("farmer", "recorded_by")[:200]
    )
    total = (
        StoreCreditTransaction.objects.filter(cooler=cooler).aggregate(
            total=Sum("total_amount_kes")
        )["total"]
        or Decimal("0.00")
    )
    return render(
        request,
        "store_credit/list.html",
        {"rows": rows, "total": total, "form": StoreCreditForm(cooler=cooler)},
    )


@role_required(Membership.Role.MANAGER, Membership.Role.CLERK)
def store_credit_create(request):
    cooler = _require_cooler(request)
    if request.method == "POST":
        form = StoreCreditForm(request.POST, cooler=cooler)
        if form.is_valid():
            txn = form.save(commit=False)
            txn.cooler = cooler
            txn.recorded_by = request.user
            txn.save()
            messages.success(
                request,
                f"Credited {txn.farmer.full_name} KES {txn.total_amount_kes} for {txn.item_name}.",
            )
            return redirect("store_credit_list")
    return render(
        request,
        "store_credit/form.html",
        {"form": StoreCreditForm(cooler=cooler), "title": "New credit sale"},
    )


# ---------------------------------------------------------------------------
# Month-end reconciliation
# ---------------------------------------------------------------------------


@role_required(Membership.Role.MANAGER)
def statement_list(request):
    cooler = _require_cooler(request)
    today = date.today()
    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))

    rows = (
        MonthlyStatement.objects.filter(cooler=cooler, year=year, month=month)
        .select_related("farmer")
        .order_by("farmer__full_name")
    )
    totals = rows.aggregate(
        litres=Sum("total_accepted_litres"),
        gross=Sum("gross_earnings_kes"),
        deducted=Sum("store_credit_deducted_kes"),
        net=Sum("net_payout_kes"),
    )

    months = list(range(1, 13))
    context = {
        "rows": rows,
        "totals": totals,
        "year": year,
        "month": month,
        "months": months,
        "month_label": calendar.month_name[month],
        "receipt_form": PayoutReceiptForm(),
        "years": range(today.year - 2, today.year + 1),
    }
    return render(request, "statements/monthly_list.html", context)


@role_required(Membership.Role.MANAGER)
@require_POST
def statement_generate(request):
    cooler = _require_cooler(request)
    try:
        year = int(request.POST.get("year", date.today().year))
        month = int(request.POST.get("month", date.today().month))
    except (TypeError, ValueError):
        messages.error(request, "Choose a valid year and month.")
        return redirect("statement_list")

    count = services.generate_cooler_statements(cooler, year, month)
    messages.success(request, f"Generated {count} statement(s) for {calendar.month_name[month]} {year}.")
    return redirect(f"{reverse('statement_list')}?year={year}&month={month}")


@role_required(Membership.Role.MANAGER)
@require_POST
def statement_mark_paid(request, pk):
    cooler = _require_cooler(request)
    statement = get_object_or_404(MonthlyStatement, pk=pk, cooler=cooler)
    form = PayoutReceiptForm(request.POST)
    if form.is_valid():
        if MonthlyStatement.objects.filter(
            coop_mpesa_payout_receipt=form.cleaned_data["receipt"]
        ).exclude(pk=statement.pk).exists():
            messages.error(request, "That M-Pesa receipt code is already recorded.")
        else:
            statement.coop_mpesa_payout_receipt = form.cleaned_data["receipt"]
            statement.payout_status = MonthlyStatement.PayoutStatus.PAID
            statement.save(
                update_fields=["coop_mpesa_payout_receipt", "payout_status", "generated_at"]
            )
            messages.success(
                request,
                f"Marked {statement.farmer.full_name} as paid "
                f"(receipt {statement.coop_mpesa_payout_receipt}).",
            )
    else:
        messages.error(request, "Enter the M-Pesa receipt code from the co-op till.")
    year = statement.year
    month = statement.month
    return redirect(f"/statements/?year={year}&month={month}")


@role_required(Membership.Role.MANAGER, Membership.Role.CLERK)
def statement_preview(request, pk):
    """Print-friendly HTML version of the statement (browser print -> PDF)."""
    cooler = _require_cooler(request)
    statement = get_object_or_404(
        MonthlyStatement.objects.select_related("farmer", "cooler"), pk=pk, cooler=cooler
    )
    return render(request, "statements/monthly_pdf.html", {"statement": statement})


@role_required(Membership.Role.MANAGER, Membership.Role.CLERK)
def statement_pdf(request, pk):
    from .pdf import render_statement_pdf

    cooler = _require_cooler(request)
    statement = get_object_or_404(
        MonthlyStatement.objects.select_related("farmer", "cooler"), pk=pk, cooler=cooler
    )
    payload = render_statement_pdf(statement)
    filename = (
        f"statement-{statement.farmer.member_number}-"
        f"{statement.year}-{statement.month:02d}.pdf"
    )
    response = HttpResponse(payload, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ---------------------------------------------------------------------------
# Daraja STK push, status poller & callback
# ---------------------------------------------------------------------------


@role_required(Membership.Role.MANAGER, Membership.Role.CLERK, Membership.Role.FARMER)
def payment_stk_push(request):
    cooler = _require_cooler(request)
    farmer = None
    if request.role == Membership.Role.FARMER:
        farmer = getattr(request.user, "farmer_profile", None)

    if request.method == "POST":
        form = STKPushForm(request.POST)
        if form.is_valid():
            member_number = request.POST.get("member_number", "")
            target = farmer
            if target is None and member_number:
                target = Farmer.objects.filter(
                    cooler=cooler, member_number=member_number
                ).first()
            try:
                txn = _initiate_stk(
                    cooler=cooler,
                    farmer=target,
                    phone=form.cleaned_data["phone_number"],
                    amount=form.cleaned_data["amount_kes"],
                    purpose=MpesaTransaction.Purpose.STORE_DEBT,
                    user=request.user,
                )
            except DarajaError as exc:
                messages.error(request, f"Daraja STK Push failed: {exc}")
            else:
                messages.success(
                    request,
                    "STK prompt sent - enter your M-Pesa PIN on the handset.",
                )
                return redirect("payment_status_page", checkout_id=txn.checkout_request_id)
    else:
        initial = {"phone_number": farmer.phone_number} if farmer else {}
        form = STKPushForm(initial=initial)

    recent = (
        MpesaTransaction.objects.filter(cooler=cooler).order_by("-created_at")[:15]
        if request.role != Membership.Role.FARMER
        else MpesaTransaction.objects.filter(cooler=cooler, farmer=farmer).order_by("-created_at")[:15]
    )
    return render(
        request,
        "payments/stk_push.html",
        {"form": form, "recent": recent, "farmer": farmer},
    )


def _initiate_stk(*, cooler, farmer, phone, amount, purpose, user):
    """Call Daraja then persist the transaction (CheckoutRequestID is unique)."""
    account_ref = MPESA_ACCOUNT_REFERENCE
    client = DarajaClient()
    response = client.initiate_stk_push(
        phone=phone,
        amount=int(amount),
        account_ref=account_ref,
        desc="Store debt" if purpose == MpesaTransaction.Purpose.STORE_DEBT else "Subscription",
    )
    checkout_id = response.get("CheckoutRequestID")
    merchant_id = response.get("MerchantRequestID")
    if not checkout_id:
        raise DarajaError(f"Unexpected Daraja response: {response}")
    txn, _ = MpesaTransaction.objects.update_or_create(
        checkout_request_id=checkout_id,
        defaults={
            "cooler": cooler,
            "farmer": farmer,
            "purpose": purpose,
            "amount_kes": amount,
            "phone_number": phone,
            "account_reference": account_ref,
            "merchant_request_id": merchant_id or "",
            "status": MpesaTransaction.Status.PENDING,
        },
    )
    return txn


@login_required
def payment_status_page(request, checkout_id):
    cooler = _require_cooler(request)
    txn = get_object_or_404(
        MpesaTransaction.objects.select_related("farmer"),
        checkout_request_id=checkout_id,
        cooler=cooler,
    )
    return render(request, "payments/status.html", {"txn": txn})


@login_required
def payment_status_api(request, checkout_id):
    """JSON polled by static/js/stk_poller.js (triggers STK Query if delayed)."""
    cooler = _require_cooler(request)
    txn = get_object_or_404(
        MpesaTransaction, checkout_request_id=checkout_id, cooler=cooler
    )

    if txn.status == MpesaTransaction.Status.PENDING:
        # Fallback poller: ask Safaricom directly when the callback is late.
        client = DarajaClient()
        try:
            result = client.query_stk_status(txn.checkout_request_id)
        except DarajaError:
            result = None
        if result:
            code = result.get("ResultCode")
            if code is not None:
                txn.result_code = int(code)
                txn.result_desc = result.get("ResultDesc", "")
                if int(code) == 0:
                    txn.status = MpesaTransaction.Status.SUCCESS
                else:
                    txn.status = MpesaTransaction.Status.FAILED
                txn.save(update_fields=["result_code", "result_desc", "status", "updated_at"])

    return JsonResponse(
        {
            "checkout_request_id": txn.checkout_request_id,
            "status": txn.status,
            "status_display": txn.get_status_display(),
            "amount_kes": str(txn.amount_kes),
            "mpesa_receipt_number": txn.mpesa_receipt_number,
            "result_desc": txn.result_desc,
            "account_reference": txn.account_reference,
        }
    )


@csrf_exempt
@require_POST
def daraja_callback(request):
    """Idempotent webhook. Always ACK so Safaricom stops retrying."""
    try:
        payload = json.loads(request.body.decode("utf-8"))
        stk_callback = payload["Body"]["stkCallback"]
        checkout_id = stk_callback["CheckoutRequestID"]
        result_code = int(stk_callback["ResultCode"])
        result_desc = stk_callback.get("ResultDesc", "")
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Invalid payload"}, status=400)

    with transaction.atomic():
        txn = (
            MpesaTransaction.objects.select_for_update()
            .filter(checkout_request_id=checkout_id)
            .first()
        )
        if not txn:
            return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

        # Idempotency guard: ignore duplicate Safaricom gateway retries.
        if txn.status == MpesaTransaction.Status.SUCCESS:
            return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

        txn.result_code = result_code
        txn.result_desc = result_desc
        txn.raw_callback_payload = payload

        if result_code == 0:
            items = stk_callback.get("CallbackMetadata", {}).get("Item", [])
            meta = {item["Name"]: item.get("Value") for item in items}
            receipt = meta.get("MpesaReceiptNumber")
            if receipt and MpesaTransaction.objects.filter(
                mpesa_receipt_number=receipt
            ).exists():
                return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})
            txn.mpesa_receipt_number = receipt
            txn.status = MpesaTransaction.Status.SUCCESS
        else:
            txn.status = MpesaTransaction.Status.FAILED
        txn.save()

    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


# ---------------------------------------------------------------------------
# Cooler settings (rates) & workspace switching
# ---------------------------------------------------------------------------


@role_required(Membership.Role.MANAGER)
def cooler_settings(request):
    cooler = _require_cooler(request)
    rates = DailyMilkRate.objects.filter(cooler=cooler)
    if request.method == "POST":
        form = DailyMilkRateForm(request.POST)
        if form.is_valid():
            rate = form.save(commit=False)
            if DailyMilkRate.objects.filter(
                cooler=cooler, effective_date=rate.effective_date
            ).exists():
                form.add_error("effective_date", "A rate already exists for that date.")
            else:
                rate.cooler = cooler
                rate.save()
                messages.success(
                    request,
                    f"Rate set to KES {rate.rate_per_litre_kes} per litre from {rate.effective_date}.",
                )
                return redirect("cooler_settings")
    else:
        form = DailyMilkRateForm()
    return render(
        request,
        "coolers/settings.html",
        {"cooler": cooler, "rates": rates, "form": form},
    )


@require_POST
@login_required
def switch_cooler(request):
    """Pick which membership workspace is active (multi-cooler staff only)."""
    cooler_id = request.POST.get("cooler_id")

    if request.user.is_superuser or request.user.is_staff:
        from .models import Cooler

        target = Cooler.objects.filter(pk=cooler_id).first() if cooler_id else None
        allowed = target is not None
    else:
        membership = Membership.objects.filter(
            user=request.user, is_active=True, cooler_id=cooler_id
        ).select_related("cooler").first()
        target = membership.cooler if membership else None
        allowed = membership is not None

    if not allowed or target is None:
        messages.error(request, "You do not have access to that cooler.")
    else:
        request.session[SESSION_KEY] = target.pk
        messages.success(request, f"Switched to {target.name}.")
    return redirect(request.META.get("HTTP_REFERER", "/dashboard/"))
