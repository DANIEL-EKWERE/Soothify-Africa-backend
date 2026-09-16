import re

from django import template
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe

register = template.Library()

_TAG = re.compile(r'(<[^>]*>)')
_WORD = re.compile(r'\b(s)(oo)(thify)', re.IGNORECASE)


@register.filter(needs_autoescape=True)
def brandmark(value, autoescape=True):
    """Give every written "Soothify" the logo's smile under its "oo".

    Plain text is escaped first. HTML (a rendered block passed through
    {% filter brandmark %}) is split on tags so only the text between them is
    touched -- never an attribute, a URL or a class name.
    """
    html = str(conditional_escape(value) if autoescape else value)
    parts = _TAG.split(html)
    for i in range(0, len(parts), 2):  # even indexes are the text between tags
        parts[i] = _WORD.sub(r'\1<span class="brand-oo">\2</span>\3', parts[i])
    return mark_safe(''.join(parts))
