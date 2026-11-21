# CoolerScopedManager scopes querysets to one cooler workspace.

# Query-level tenant isolation helper: a queryset created through
# ``Cooler.objects.for_cooler(cooler)``-style scoping can only ever see rows
# belonging to one cooler, so a leaked ``cooler_id`` cannot cross workspaces.


from django.db import models


class CoolerScopedQuerySet(models.QuerySet):
    def for_cooler(self, cooler):
        return self.filter(cooler=cooler)


class CoolerScopedManager(models.Manager.from_queryset(CoolerScopedQuerySet)):
    """Usage: MilkIntake.scoped.objects.for_cooler(request.cooler)"""

    def for_cooler(self, cooler):
        if cooler is None:
            return self.none()
        return self.filter(cooler=cooler)


class CoolerScopedModel(models.Model):
    """Base class adding ``objects`` scoped to a single cooler."""

    objects = CoolerScopedManager()

    class Meta:
        abstract = True
