"""Template filters for rendering Bootstrap-styled form fields."""

from django import template

register = template.Library()


@register.filter
def addclass(field, css):
    """Append CSS classes to a bound form field's widget."""
    if not hasattr(field, "as_widget"):
        return field
    existing = field.field.widget.attrs.get("class", "")
    classes = f"{existing} {css}".strip()
    return field.as_widget(attrs={**field.field.widget.attrs, "class": classes})
