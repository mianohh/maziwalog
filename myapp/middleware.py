# ActiveCoolerMiddleware resolves the active cooler workspace per request.

# Resolves the tenant workspace for every request and attaches:

#     request.cooler     the active Cooler (or None)
#     request.membership the user's Membership in that cooler (or None)
#     request.role       MANAGER | CLERK | FARMER | STAFF | ANONYMOUS

# Isolation contract: every domain query is filtered by
# ``request.cooler``, and cross-tenant lookups raise HTTP 404.


from django.shortcuts import render

from .models import Membership

ANONYMOUS = "ANONYMOUS"
STAFF = "STAFF"
SESSION_KEY = "active_cooler_id"


class ActiveCoolerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._attach(request)
        response = self.get_response(request)
        return response

    # ------------------------------------------------------------------
    def _attach(self, request):
        request.cooler = None
        request.membership = None
        request.role = ANONYMOUS

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            # API calls authenticate with an ``Authorization:
            # Token ...`` rather than a session, so resolve that user here -
            # otherwise the cooler workspace would never be attached and every
            # REST endpoint would answer 403.
            user = self._token_user(request)
        if user is None or not user.is_authenticated:
            return

        memberships = Membership.objects.select_related("cooler").filter(
            user=user, is_active=True
        )

        if user.is_superuser or user.is_staff:
            request.role = STAFF
            cooler = self._session_cooler(request)
            if cooler is None and memberships:
                cooler = memberships.first().cooler
            if cooler is None:
                from .models import Cooler

                cooler = Cooler.objects.order_by("id").first()
            request.cooler = cooler
            request.membership = memberships.filter(cooler=cooler).first()
            return

        if not memberships.exists():
            # Staff-like accounts created straight in admin have no role yet.
            request.role = ANONYMOUS
            return

        membership = None
        session_cooler = self._session_cooler(request)
        if session_cooler is not None:
            membership = memberships.filter(cooler=session_cooler).first()

        if membership is None:
            # Prefer a farmer profile so farmers land in their own cooler.
            membership = memberships.filter(role=Membership.Role.FARMER).first()
        if membership is None:
            membership = memberships.first()

        request.membership = membership
        request.cooler = membership.cooler
        request.role = membership.role

    @staticmethod
    def _token_user(request):
        """Return the user behind an ``Authorization: Token <key>`` header."""
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.lower().startswith("token "):
            return None
        key = header.split(None, 1)[1].strip()
        if not key:
            return None
        from rest_framework.authtoken.models import Token

        token = Token.objects.select_related("user").filter(key=key).first()
        return token.user if token else None

    @staticmethod
    def _session_cooler(request):
        from .models import Cooler

        cooler_id = request.session.get(SESSION_KEY)
        if cooler_id is None:
            return None
        return Cooler.objects.filter(pk=cooler_id).first()

    # ------------------------------------------------------------------
    @staticmethod
    def permission_denied(request, message=None):
        """404 page used for cross-tenant access attempts."""
        return render(
            request,
            "404.html",
            {"message": message or "That record does not exist in this cooler workspace."},
            status=404,
        )
