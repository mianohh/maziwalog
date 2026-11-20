"""Role-aware API permissions."""

from rest_framework.permissions import SAFE_METHODS, BasePermission

from ..models import Membership

MANAGER_CLERK = (Membership.Role.MANAGER, Membership.Role.CLERK)
FARMER_CLERK = (Membership.Role.FARMER, Membership.Role.CLERK)


class IsCoolerMember(BasePermission):
    """The requester must hold an active role in the active cooler workspace."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_superuser or user.is_staff:
            return True
        return getattr(request, "role", None) in {
            Membership.Role.MANAGER,
            Membership.Role.CLERK,
            Membership.Role.FARMER,
        } and getattr(request, "cooler", None) is not None


class HasRole(IsCoolerMember):
    """Allow only the roles declared on the view (``view.allowed_roles``)."""

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        allowed = getattr(view, "allowed_roles", None)
        if not allowed:
            return True
        if request.user.is_superuser or request.user.is_staff:
            return True
        return getattr(request, "role", None) in allowed


class IsWriteRole(HasRole):
    """SAFE_METHODS are open to every member; writes need ``allowed_roles``."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return IsCoolerMember.has_permission(self, request, view)
        return super().has_permission(request, view)
