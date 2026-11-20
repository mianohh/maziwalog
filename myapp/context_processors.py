"""Template context: active workspace, memberships and role badges."""

from .models import DailyMilkRate, Membership

ROLE_BADGES = {
    Membership.Role.MANAGER: "Manager",
    Membership.Role.CLERK: "Intake Clerk",
    Membership.Role.FARMER: "Farmer",
}


def workspace(request):
    user = getattr(request, "user", None)
    memberships = []
    if user is not None and user.is_authenticated:
        memberships = [
            {"cooler": m.cooler, "role": m.role, "badge": ROLE_BADGES.get(m.role, m.role)}
            for m in Membership.objects.select_related("cooler")
            .filter(user=user, is_active=True)
            .order_by("cooler__name")
        ]
    cooler = getattr(request, "cooler", None)
    return {
        "workspace_memberships": memberships,
        "active_cooler": cooler,
        "active_role": getattr(request, "role", "ANONYMOUS"),
        "role_badge": ROLE_BADGES.get(getattr(request, "role", None), ""),
        "active_rate": DailyMilkRate.rate_for(cooler) if cooler else None,
    }
