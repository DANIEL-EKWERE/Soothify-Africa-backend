from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

# One turn of the wheel. The five designed cards repeat around the full circle so
# there is never a gap; only the top arc is ever visible.
WHEEL_CARDS = [
    {
        'slug': 'music',
        'img': 'img/card-music.jpg',
        'alt': _('Person listening to calming music'),
        'title': _('Relaxation Music'),
        'body': _('Soft, grounding sounds to help quiet a loud day'),
    },
    {
        'slug': 'pilates',
        'img': 'img/card-pilates.jpg',
        'alt': _('Guided Pilates and stretching session'),
        'title': _('Virtual Pilates & Stretch'),
        'body': _('Low-impact movement and guided Pilates sessions to build core '
                  'strength and shake off physical tension'),
    },
    {
        'slug': 'therapy',
        'img': 'img/card-therapy.webp',
        'alt': _('Woman in an online therapy session'),
        'title': _('One-on-One Therapy Sessions'),
        'body': _('Safe, confidential spaces to talk through things with licensed '
                  'professionals who actually get it'),
    },
    {
        'slug': 'meditate',
        'img': 'img/card-meditation.jpg',
        'alt': _('Person meditating'),
        'title': _('Guided Meditation'),
        'body': _('Simple, bite-sized practices to help you catch your breath'),
    },
    {
        'slug': 'articles',
        'img': 'img/card-articles.jpg',
        'alt': _('Reading a wellness article'),
        'title': _('Wellness Articles'),
        'body': _('Real, straightforward reads on rest, mental health, and everyday ease.'),
    },
]

SLOT_DEGREES = 12   # angular gap between neighbouring cards
FAN_REACH = 4       # slots either side of centre; ±48° is already off the visible arc
CENTRE = 2          # index of the card that sits at the top of the arc (therapy, as designed)
TURN_PER_SLOT = 18  # degrees of self-spin per slot from centre, during the entrance only


def _wheel():
    """The designed cards laid along the top arc, centre outwards, each with its
    own signed angle. Slots beyond the five designed cards repeat the sequence so
    the arc stays full on wide screens; those repeats are decorative only.

    One extra slot sits past the right-hand end (-48° .. +60°). The carousel
    steps every card one slot left and moves the card leaving the left end to
    that spare slot, so ten slots -- a whole number of turns of the five-card
    sequence -- keep neighbours in order however long it runs."""
    cards = []
    for offset in range(-FAN_REACH, FAN_REACH + 2):
        card = dict(WHEEL_CARDS[(CENTRE + offset) % len(WHEEL_CARDS)])
        card['angle'] = offset * SLOT_DEGREES
        # the fan opens from the centre, so cards further out start later
        card['delay'] = abs(offset) * 70
        # how far the card spins on its own axis on the way out; the sign flips
        # either side of centre so the two halves counter-turn as they open
        card['turn'] = -offset * TURN_PER_SLOT
        card['decorative'] = abs(offset) > len(WHEEL_CARDS) // 2
        cards.append(card)
    return cards


def landing(request):
    return render(request, 'home/landing.html', {'wheel_cards': _wheel()})


# The policy pages are static prose; the date is the one the copy was approved
# with, so it is stated here rather than generated from "now".
LEGAL_UPDATED = _('September 2026')


def privacy(request):
    return render(request, 'home/privacy.html', {
        'page_title': _('Privacy Policy'),
        'last_updated': LEGAL_UPDATED,
    })


def terms(request):
    return render(request, 'home/terms.html', {
        'page_title': _('Terms of Use'),
        'last_updated': LEGAL_UPDATED,
    })
