from django.conf import settings


def site(request):
    """Values every template needs, including the ones rendered by the shared
    nav and footer partials on pages whose views know nothing about them."""
    return {'waitlist_url': settings.WAITLIST_URL}
