"""
Google OAuth profile hydration.

Copies the Google display name into User.full_name on the first social signup.
"""

from allauth.account.signals import user_signed_up
from django.dispatch import receiver


@receiver(user_signed_up)
def populate_user_from_google(request, user, **kwargs):
    sociallogin = kwargs.get("sociallogin")
    if sociallogin and sociallogin.account.provider == "google":
        extra_data = sociallogin.account.extra_data
        full_name = extra_data.get("name", "")
        if full_name and not user.full_name:
            user.full_name = full_name
            user.save(update_fields=["full_name"])
