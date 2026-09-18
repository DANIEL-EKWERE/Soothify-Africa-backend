from django.urls import reverse


def site(request):
    """Values every template needs, including the ones rendered by the shared
    nav and footer partials on pages whose views know nothing about them."""
    # Resolved per request so it carries the visitor's language prefix (/pcm/...).
    return {'waitlist_url': reverse('home:waitlist')}
