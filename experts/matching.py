"""Picking the expert a client is matched with.

The app shows a three-second "Finding your Pilates instructor" interstitial
and then a card with a match percentage. That percentage was decoration over
a hardcoded coach; here it is the result of an actual choice, so the number
means something.

Deliberately simple and explainable. A client should be able to be told why
they were matched, and a score nobody can account for is worse than a plain
rule -- particularly for therapy.
"""

from django.utils import timezone

from .models import AvailabilitySlot, ExpertProfile, Session

# What the score is made of, out of 100. Availability dominates: an expert who
# cannot see you this week is not a good match however well you otherwise fit.
WEIGHTS = {
    'discipline': 40,     # practises the thing that was asked for
    'available': 30,      # has the chosen slot open
    'language': 15,       # can run the session in the client's language
    'rating': 10,         # how previous clients rated them
    'workload': 5,        # fewer sessions this week, so attention is spread
}


def candidates(discipline, day=None, start_minute=None, language=None):
    """Experts who could take this booking, best first.

    Returns (expert, score) pairs. An empty list means nobody is free, which
    the caller must handle -- offering a different time is a better answer
    than booking someone who is busy.
    """
    experts = ExpertProfile.objects.filter(
        is_published=True, is_accepting=True,
    ).select_related('profile')

    scored = []
    for expert in experts:
        if not expert.practises(discipline):
            continue
        score = WEIGHTS['discipline']

        free = _is_free(expert, day, start_minute)
        if day is not None and not free:
            # Not a candidate at all for that hour, rather than a poor one.
            continue
        if free:
            score += WEIGHTS['available']

        if language and language in (expert.languages or []):
            score += WEIGHTS['language']

        if expert.rating_count:
            score += WEIGHTS['rating'] * (expert.rating_average / 5.0)

        score += WEIGHTS['workload'] * _spare_capacity(expert)
        scored.append((expert, int(round(score))))

    scored.sort(key=lambda pair: (-pair[1], pair[0].pk))
    return scored


def best(discipline, day=None, start_minute=None, language=None):
    """The one expert to offer, or None when nobody is free."""
    found = candidates(discipline, day, start_minute, language)
    return found[0] if found else (None, 0)


def _is_free(expert, day, start_minute):
    """Open in their availability, and not already booked then."""
    if day is None or start_minute is None:
        return True

    open_then = AvailabilitySlot.objects.filter(
        expert=expert,
        day=day,
        start_minute__lte=start_minute,
        end_minute__gt=start_minute,
    ).exists()
    if not open_then:
        return False

    return not Session.objects.filter(
        expert=expert,
        starts_at__date=day,
        status__in=('pending', 'confirmed'),
    ).filter(
        starts_at__hour=start_minute // 60,
        starts_at__minute=start_minute % 60,
    ).exists()


def _spare_capacity(expert):
    """1.0 for an empty week, falling towards 0 as it fills.

    Caps at twenty sessions a week: beyond that the difference between busy
    and very busy does not matter to the person being matched.
    """
    now = timezone.now()
    week_start = now - timezone.timedelta(days=now.weekday())
    booked = expert.sessions.filter(
        starts_at__gte=week_start, status__in=('pending', 'confirmed'),
    ).count()
    return max(0.0, 1.0 - min(booked, 20) / 20.0)
