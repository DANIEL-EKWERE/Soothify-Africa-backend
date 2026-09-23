import re

from django import template
from django.urls import translate_url
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


@register.simple_tag(takes_context=True)
def path_in_language(context, code):
    """"/pcm/waitlist/" -> "/waitlist/" for code "en", and back again.

    The language switcher posts this as its "next", so the visitor lands on the
    same page in the language they picked. Django's own set_language would work
    this out for itself, but only for a visitor who already has a language
    cookie: it translates the URL under the language it thinks is active, and
    without a cookie that is English, under which "/pcm/..." does not resolve at
    all -- so someone who opened a shared /pcm/ link could never leave Pidgin.
    Here the page's own language is active, so the address always translates.
    """
    request = context.get('request')
    if request is None:
        return '/'
    return translate_url(request.get_full_path(), code)
