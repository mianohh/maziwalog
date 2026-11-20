"""
Seed the database with realistic MaziwaLog demo data.

Order of creation mirrors the domain dependencies:
coolers -> managers/clerks/farmers -> daily rates -> milk intakes
-> store credit -> M-Pesa transactions -> monthly statements.

Safe to re-run: every row is keyed on its natural key via get_or_create.

Usage:
    python manage.py seed_demo
"""

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from myapp import services
from myapp.models import (
    Cooler,
    DailyMilkRate,
    Farmer,
    Membership,
    MilkIntake,
    MonthlyStatement,
    MpesaTransaction,
    MPESA_ACCOUNT_REFERENCE,
    StoreCreditTransaction,
    User,
)

PASSWORD = "maziwalog123"

COOLERS = [
    {
        "name": "Nyota Dairy Cooler",
        "county": "Kiambu",
        "till_or_paybill": "522522",
        "account_prefix": "NZ",
        "manager": ("Wanjiku Kamau", "manager.nyota@maziwalog.co.ke", "254722000001"),
        "clerks": [
            ("Peter Otieno", "clerk1.nyota@maziwalog.co.ke", "254722000002"),
            ("Grace Wairimu", "clerk2.nyota@maziwalog.co.ke", "254722000003"),
        ],
        "farmers": [
            ("Kamau Mwangi", "254722111001", "NZ-001"),
            ("Njeri Wanjiru", "254722111002", "NZ-002"),
            ("Ochieng Odhiambo", "254722111003", "NZ-003"),
            ("Achieng Akinyi", "254722111004", "NZ-004"),
            ("Kipchoge Rono", "254722111005", "NZ-005"),
            ("Wambui Njoroge", "254722111006", "NZ-006"),
        ],
    },
    {
        "name": "Maziwa Bora Collection Centre",
        "county": "Nakuru",
        "till_or_paybill": "400200",
        "account_prefix": "MB",
        "manager": ("Samuel Kiptoo", "manager.bora@maziwalog.co.ke", "254733000001"),
        "clerks": [
            ("Lucy Nafula", "clerk1.bora@maziwalog.co.ke", "254733000002"),
            ("John Mutiso", "clerk2.bora@maziwalog.co.ke", "254733000003"),
        ],
        "farmers": [
            ("Daniel Kiprop", "254733111001", "MB-001"),
            ("Mercy Chebet", "254733111002", "MB-002"),
            ("Hassan Abdi", "254733111003", "MB-003"),
            ("Faith Mutindi", "254733111004", "MB-004"),
            ("Josphat Maina", "254733111005", "MB-005"),
            ("Agnes Chelimo", "254733111006", "MB-006"),
        ],
    },
]

RATE_SCHEDULE = [  # (days_ago, rate_per_litre_kes)
    (60, Decimal("48.00")),
    (30, Decimal("50.00")),
    (7, Decimal("52.00")),
]

STORE_ITEMS = [
    ("Dairy Meal 70kg", Decimal("1"), Decimal("2800.00")),
    ("Mineral Salt Block", Decimal("2"), Decimal("450.00")),
    ("Urea Molasses Block", Decimal("1"), Decimal("1200.00")),
    ("Calf Milk Replacer 10kg", Decimal("1"), Decimal("1650.00")),
    ("Acaricide Dip 1L", Decimal("1"), Decimal("950.00")),
]

INTAKE_DAYS = 28


class Command(BaseCommand):
    help = "Populate the database with demo coolers, staff, farmers, intakes, credit and statements."

    def handle(self, *args, **options):
        today = timezone.localdate()
        with transaction.atomic():
            admin, _ = User.objects.get_or_create(
                email="admin@maziwalog.co.ke",
                defaults={
                    "full_name": "MaziwaLog Admin",
                    "phone_number": "254700000000",
                    "is_staff": True,
                    "is_superuser": True,
                },
            )
            if not admin.check_password(PASSWORD):
                admin.set_password(PASSWORD)
                admin.save(update_fields=["password"])

            totals = {
                "coolers": 0,
                "managers": 0,
                "clerks": 0,
                "farmers": 0,
                "rates": 0,
                "intakes": 0,
                "credit_sales": 0,
                "mpesa_txns": 0,
                "statements": 0,
            }

            for spec in COOLERS:
                counts = self._seed_cooler(spec, today)
                for key, value in counts.items():
                    totals[key] += value

        self.stdout.write(self.style.SUCCESS("Seed complete."))
        for key, value in totals.items():
            self.stdout.write(f"  {key:<14}: {value}")
        self.stdout.write("")
        self.stdout.write("Logins (password: %s):" % PASSWORD)
        self.stdout.write("  admin@maziwalog.co.ke (superuser)")
        for spec in COOLERS:
            self.stdout.write(f"  {spec['manager'][1]} (manager, {spec['name']})")
            for full_name, email, _phone in spec["clerks"]:
                self.stdout.write(f"  {email} (clerk, {spec['name']})")
            for full_name, phone, member_no in spec["farmers"]:
                self.stdout.write(f"  farmer.{member_no.lower()}@maziwalog.co.ke (farmer, {member_no})")

    # ------------------------------------------------------------------
    def _seed_cooler(self, spec, today):
        counts = {k: 0 for k in (
            "coolers", "managers", "clerks", "farmers",
            "rates", "intakes", "credit_sales", "mpesa_txns", "statements",
        )}

        cooler, created = Cooler.objects.get_or_create(
            name=spec["name"],
            defaults={
                "county": spec["county"],
                "till_or_paybill": spec["till_or_paybill"],
                "account_prefix": spec["account_prefix"],
            },
        )
        counts["coolers"] += int(created)

        # -- manager & clerks -----------------------------------------
        staff = []
        for role, person in (
            [(Membership.Role.MANAGER, spec["manager"])]
            + [(Membership.Role.CLERK, p) for p in spec["clerks"]]
        ):
            full_name, email, phone = person
            user, _ = User.objects.get_or_create(
                email=email,
                defaults={"full_name": full_name, "phone_number": phone},
            )
            if not user.check_password(PASSWORD):
                user.set_password(PASSWORD)
                user.save(update_fields=["password"])
            membership, m_created = Membership.objects.get_or_create(
                user=user, cooler=cooler, defaults={"role": role}
            )
            if role == Membership.Role.MANAGER:
                counts["managers"] += int(m_created)
            else:
                counts["clerks"] += int(m_created)
            staff.append(user)
        clerks = staff[1:]

        # -- daily milk rates ------------------------------------------
        for days_ago, rate in RATE_SCHEDULE:
            _, r_created = DailyMilkRate.objects.get_or_create(
                cooler=cooler,
                effective_date=today - timedelta(days=days_ago),
                defaults={"rate_per_litre_kes": rate},
            )
            counts["rates"] += int(r_created)

        # -- farmers ---------------------------------------------------
        farmer_records = []
        for idx, (full_name, phone, member_no) in enumerate(spec["farmers"]):
            email = f"farmer.{member_no.lower()}@maziwalog.co.ke"
            user, _ = User.objects.get_or_create(
                email=email,
                defaults={"full_name": full_name, "phone_number": phone},
            )
            if not user.check_password(PASSWORD):
                user.set_password(PASSWORD)
                user.save(update_fields=["password"])
            farmer, f_created = Farmer.objects.get_or_create(
                cooler=cooler,
                member_number=member_no,
                defaults={
                    "user": user,
                    "full_name": full_name,
                    "phone_number": phone,
                    "national_id": f"2{10000000 + cooler.pk * 1000 + idx}",
                },
            )
            counts["farmers"] += int(f_created)
            Membership.objects.get_or_create(
                user=user, cooler=cooler, defaults={"role": Membership.Role.FARMER}
            )
            farmer_records.append((farmer, idx))

        # -- milk intakes (last INTAKE_DAYS, AM + PM) -------------------
        base_litres = [Decimal(x) for x in ("18.50", "24.00", "11.25", "31.75", "9.00", "27.40", "14.80", "21.30")]
        window_start = today - timedelta(days=INTAKE_DAYS - 1)
        existing_intakes = set(
            MilkIntake.objects.filter(
                cooler=cooler, collection_date__gte=window_start
            ).values_list("farmer_id", "collection_date", "session")
        )
        intake_batch = []
        for farmer, idx in farmer_records:
            base = base_litres[idx % len(base_litres)]
            for days_ago in range(INTAKE_DAYS):
                day = today - timedelta(days=days_ago)
                for session_offset, session in enumerate(
                    (MilkIntake.Session.MORNING, MilkIntake.Session.EVENING)
                ):
                    if (farmer.pk, day, session) in existing_intakes:
                        continue
                    # deterministic occasional gap (dry day / no delivery)
                    if (idx * 3 + days_ago + session_offset) % 17 == 0:
                        continue
                    swing = Decimal(((idx * 7 + days_ago * 3 + session_offset) % 5) - 2) * Decimal("0.60")
                    accepted = max(Decimal("1.25"), base + swing)
                    if session == MilkIntake.Session.EVENING:
                        accepted = (accepted * Decimal("0.72")).quantize(Decimal("0.01"))
                    else:
                        accepted = accepted.quantize(Decimal("0.01"))
                    rejected = Decimal("0.00")
                    reason = ""
                    if (idx + days_ago) % 23 == 0:
                        rejected = Decimal("1.20")
                        reason = "High water content (added water test positive)"
                    intake_batch.append(
                        MilkIntake(
                            cooler=cooler,
                            farmer=farmer,
                            collection_date=day,
                            session=session,
                            accepted_litres=accepted,
                            rejected_litres=rejected,
                            rejection_reason=reason,
                            recorded_by=clerks[(days_ago + session_offset) % len(clerks)],
                        )
                    )
        MilkIntake.objects.bulk_create(intake_batch, batch_size=500, ignore_conflicts=True)
        counts["intakes"] += len(intake_batch)

        # -- store credit ----------------------------------------------
        existing_credit = set(
            StoreCreditTransaction.objects.filter(cooler=cooler).values_list(
                "farmer_id", "item_name", "transaction_date"
            )
        )
        credit_batch = []
        for farmer, idx in farmer_records:
            for n, (item_name, qty, unit_price) in enumerate(STORE_ITEMS[: 2 + (idx % 3)]):
                txn_day = today - timedelta(days=20 - n * 6 - (idx % 4))
                if (farmer.pk, item_name, txn_day) in existing_credit:
                    continue
                credit_batch.append(
                    StoreCreditTransaction(
                        cooler=cooler,
                        farmer=farmer,
                        item_name=item_name,
                        quantity=qty,
                        unit_price_kes=unit_price,
                        total_amount_kes=qty * unit_price,
                        recorded_by=staff[0],
                        transaction_date=txn_day,
                    )
                )
        StoreCreditTransaction.objects.bulk_create(credit_batch, batch_size=500, ignore_conflicts=True)
        counts["credit_sales"] += len(credit_batch)

        # -- M-Pesa transactions (receipts log) -------------------------
        mpesa_rows = [
            ("STORE_DEBT", Decimal("2500.00"), MpesaTransaction.Status.SUCCESS, "SFF7XY001Z1", 0),
            ("STORE_DEBT", Decimal("1200.00"), MpesaTransaction.Status.SUCCESS, "SFF7XY002Z2", 3),
            ("STORE_DEBT", Decimal("800.00"), MpesaTransaction.Status.FAILED, None, 6),
            ("SUBSCRIPTION", Decimal("1500.00"), MpesaTransaction.Status.SUCCESS, "SFF7XY003Z3", 10),
            ("SUBSCRIPTION", Decimal("1500.00"), MpesaTransaction.Status.PENDING, None, 1),
        ]
        existing_checkouts = set(
            MpesaTransaction.objects.filter(
                checkout_request_id__startswith=f"ws_CO_SEED_{cooler.pk:02d}_"
            ).values_list("checkout_request_id", flat=True)
        )
        mpesa_batch = []
        for n, (purpose, amount, status, receipt, _days_ago) in enumerate(mpesa_rows):
            checkout_id = f"ws_CO_SEED_{cooler.pk:02d}_{n:03d}"
            if checkout_id in existing_checkouts:
                continue
            farmer = farmer_records[n % len(farmer_records)][0]
            mpesa_batch.append(
                MpesaTransaction(
                    cooler=cooler,
                    farmer=farmer if purpose == MpesaTransaction.Purpose.STORE_DEBT else None,
                    purpose=purpose,
                    amount_kes=amount,
                    phone_number=farmer.phone_number,
                    account_reference=MPESA_ACCOUNT_REFERENCE,
                    merchant_request_id=f"29115-{3000000 + cooler.pk * 100 + n}-1",
                    checkout_request_id=checkout_id,
                    mpesa_receipt_number=(f"{receipt}{cooler.pk:02d}" if receipt and status == MpesaTransaction.Status.SUCCESS else None),
                    result_code=0 if status == MpesaTransaction.Status.SUCCESS else (1 if status == MpesaTransaction.Status.FAILED else None),
                    result_desc={
                        MpesaTransaction.Status.SUCCESS: "The service request is processed successfully.",
                        MpesaTransaction.Status.FAILED: "Request cancelled by user",
                        MpesaTransaction.Status.PENDING: "",
                    }[status],
                    status=status,
                    raw_callback_payload={"seed": True},
                )
            )
        MpesaTransaction.objects.bulk_create(mpesa_batch, batch_size=500, ignore_conflicts=True)
        counts["mpesa_txns"] += len(mpesa_batch)

        # -- monthly statements ----------------------------------------
        first_of_month = today.replace(day=1)
        last_month = first_of_month - timedelta(days=1)
        for y, m in ((last_month.year, last_month.month), (today.year, today.month)):
            counts["statements"] += services.generate_cooler_statements(cooler, y, m)

        # Mark some older statements as paid to make the dashboard realistic.
        for statement in MonthlyStatement.objects.filter(
            cooler=cooler, year=last_month.year, month=last_month.month
        ).exclude(payout_status=MonthlyStatement.PayoutStatus.DEBT_CARRYOVER):
            statement.payout_status = MonthlyStatement.PayoutStatus.PAID
            statement.coop_mpesa_payout_receipt = (
                f"PAY{cooler.pk:02d}{statement.farmer_id:04d}{last_month.month:02d}"
            )
            statement.save(update_fields=["payout_status", "coop_mpesa_payout_receipt"])

        return counts
