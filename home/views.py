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
        'img': 'img/card-therapy.jpg',
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

SLOT_DEGREES = 12          # angular gap between neighbouring cards
SLOTS = 360 // SLOT_DEGREES  # 30 cards fill the circle


def _wheel():
    """The 5 designed cards repeated around 360°, each with its own angle."""
    cards = []
    for slot in range(SLOTS):
        card = dict(WHEEL_CARDS[slot % len(WHEEL_CARDS)])
        card['angle'] = slot * SLOT_DEGREES
        # only the first turn is exposed to assistive tech; the rest are decorative
        card['decorative'] = slot >= len(WHEEL_CARDS)
        cards.append(card)
    return cards


def landing(request):
    return render(request, 'home/landing.html', {'wheel_cards': _wheel()})
