"""The staff dashboard.

Separate from Django's admin on purpose. The admin is a database editor: it
shows every table and every column, in the order the models declare them.
This shows the four things the team actually does -- see who signed up, put
content in the library, keep an eye on sessions, and review what a recording
flagged -- in the house style.

Everything here is staff-only. The gate is one decorator, applied to every
view, and a test asserts that an ordinary account bounces off each one.
"""

import csv
import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from billing.models import Membership, Payment
from content.models import Category, MediaItem, PlaybackEvent, Shelf
from corporate.models import Enquiry
from experts.models import ExpertProfile, Session
from home.models import WaitlistSignup
from recordings.models import AccessLog, Flag, Recording
from spaces.models import PassportWaitlistEntry

from .forms import MediaItemForm

PAGE_SIZE = 25


LOGIN_PAGE = '/dashboard/login/'


def staff_only(view):
    """Signed in, and staff. Anyone else is sent to the sign-in page.

    Both decorators are given the login page explicitly: without it the inner
    one falls back to Django's default, /accounts/login/, which this project
    does not serve -- so a signed-in member who found the URL would be sent
    to a 404 rather than told to sign in as somebody who may be here.
    """
    gate = user_passes_test(lambda user: user.is_staff, login_url=LOGIN_PAGE)
    return login_required(gate(view), login_url=LOGIN_PAGE)


@staff_only
def overview(request):
    """What happened lately, at a glance.

    Counts over a fortnight rather than all time: "12 this week" tells you
    something, "3,412 ever" stops meaning anything by the second month.
    """
    now = timezone.now()
    week_ago = now - datetime.timedelta(days=7)
    fortnight_ago = now - datetime.timedelta(days=14)

    signups = WaitlistSignup.objects.all()
    this_week = signups.filter(created_at__gte=week_ago).count()
    week_before = signups.filter(
        created_at__gte=fortnight_ago, created_at__lt=week_ago,
    ).count()

    sessions = Session.objects.filter(status__in=('confirmed', 'completed'))
    minutes = PlaybackEvent.objects.aggregate(total=Sum('seconds_played'))['total'] or 0

    cards = [
        {
            'label': 'Waitlist',
            'value': signups.count(),
            'note': '{0} this week'.format(this_week),
            'trend': _trend(this_week, week_before),
            'href': '/dashboard/waitlist/',
        },
        {
            'label': 'Accounts',
            'value': get_user_model().objects.count(),
            'note': '{0} on a membership'.format(
                Membership.objects.filter(state__in=('trial', 'active')).count(),
            ),
            'href': '/dashboard/people/',
        },
        {
            'label': 'In the library',
            'value': MediaItem.objects.live().count(),
            'note': '{0} minutes played'.format(minutes // 60),
            'href': '/dashboard/media/',
        },
        {
            'label': 'Sessions booked',
            'value': sessions.count(),
            'note': '{0} still to come'.format(
                sessions.filter(starts_at__gte=now, status='confirmed').count(),
            ),
            'href': '/dashboard/sessions/',
        },
    ]

    open_flags = Flag.objects.filter(status='new').count()

    return render(request, 'dashboard/overview.html', {
        'page': 'overview',
        'cards': cards,
        'open_flags': open_flags,
        'bars': _signups_by_day(signups, now),
        'recent_signups': signups.order_by('-created_at')[:6],
        'recent_media': MediaItem.objects.order_by('-created_at')[:5],
        'upcoming': sessions.filter(starts_at__gte=now).order_by('starts_at')[:5],
        'enquiries': Enquiry.objects.filter(status='new').count(),
        'passport': PassportWaitlistEntry.objects.count(),
    })


@staff_only
def waitlist(request):
    """Who has asked to be told, and where they came from."""
    rows = WaitlistSignup.objects.all().order_by('-created_at')

    term = (request.GET.get('q') or '').strip()
    if term:
        rows = rows.filter(name__icontains=term) | rows.filter(email__icontains=term)

    source = request.GET.get('source') or ''
    if source:
        rows = rows.filter(source=source)

    return render(request, 'dashboard/waitlist.html', {
        'page': 'waitlist',
        'rows': rows[:200],
        'total': rows.count(),
        'q': term,
        'source': source,
        'sources': WaitlistSignup.objects.values('source').annotate(
            count=Count('id'),
        ).order_by('-count'),
        'passport': PassportWaitlistEntry.objects.order_by('-created_at')[:50],
    })


@staff_only
def waitlist_csv(request):
    """The whole list, for a spreadsheet."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="soothify-waitlist.csv"'

    writer = csv.writer(response)
    writer.writerow(['name', 'email', 'phone', 'source', 'wants updates', 'language', 'joined'])
    for row in WaitlistSignup.objects.order_by('created_at'):
        writer.writerow([
            row.name, row.email, row.phone, row.get_source_display(),
            'yes' if row.wants_updates else 'no', row.language,
            row.created_at.strftime('%Y-%m-%d %H:%M'),
        ])
    return response


@staff_only
def media_list(request):
    """The library."""
    items = MediaItem.objects.select_related('category').order_by('-created_at')

    kind = request.GET.get('type') or ''
    if kind:
        items = items.filter(media_type=kind)

    term = (request.GET.get('q') or '').strip()
    if term:
        items = items.search(term)

    return render(request, 'dashboard/media_list.html', {
        'page': 'media',
        'items': items[:PAGE_SIZE * 4],
        'total': items.count(),
        'q': term,
        'type': kind,
        'counts': MediaItem.objects.values('media_type').annotate(count=Count('id')),
        'categories': Category.objects.filter(is_active=True),
        'shelves': Shelf.objects.filter(is_active=True),
    })


@staff_only
def media_new(request):
    """Upload something."""
    form = MediaItemForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        item = form.save()
        return redirect('/dashboard/media/?saved={0}'.format(item.pk))

    return render(request, 'dashboard/media_form.html', {
        'page': 'media',
        'form': form,
        'item': None,
    })


@staff_only
def media_edit(request, pk):
    item = get_object_or_404(MediaItem, pk=pk)
    form = MediaItemForm(request.POST or None, request.FILES or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('/dashboard/media/?saved={0}'.format(item.pk))

    return render(request, 'dashboard/media_form.html', {
        'page': 'media',
        'form': form,
        'item': item,
    })


@staff_only
@require_POST
def media_publish(request, pk):
    """Publish or unpublish, without opening the whole form."""
    item = get_object_or_404(MediaItem, pk=pk)
    item.is_published = not item.is_published
    item.save(update_fields=['is_published', 'updated_at'])
    return redirect('/dashboard/media/')


@staff_only
def sessions(request):
    """Bookings, soonest first."""
    rows = Session.objects.select_related(
        'client', 'expert', 'expert__profile',
    ).order_by('-starts_at')

    state = request.GET.get('status') or ''
    if state:
        rows = rows.filter(status=state)

    return render(request, 'dashboard/sessions.html', {
        'page': 'sessions',
        'rows': rows[:PAGE_SIZE * 2],
        'total': rows.count(),
        'status': state,
        'experts': ExpertProfile.objects.select_related('profile'),
        'earned': Payment.objects.filter(kind='session', status='paid').aggregate(
            total=Sum('amount_naira'),
        )['total'] or 0,
    })


@staff_only
def recordings(request):
    """Session recordings and what they flagged.

    The list says a recording exists and whether anything was flagged. It
    does not play anything: opening audio is a separate, logged act, and it
    belongs on the flag you are actually reviewing.
    """
    rows = Recording.objects.select_related('session', 'session__client').annotate(
        flag_count=Count('flags'),
    ).order_by('-created_at')

    return render(request, 'dashboard/recordings.html', {
        'page': 'recordings',
        'rows': rows[:PAGE_SIZE * 2],
        'total': rows.count(),
        'flags': Flag.objects.filter(status='new').select_related('recording')[:20],
        'open_flags': Flag.objects.filter(status='new').count(),
        'recent_access': AccessLog.objects.select_related('staff')[:10],
    })


@staff_only
def people(request):
    """Accounts, and what sort each is."""
    rows = get_user_model().objects.select_related('profile').order_by('-date_joined')

    term = (request.GET.get('q') or '').strip()
    if term:
        rows = rows.filter(email__icontains=term) | rows.filter(
            profile__full_name__icontains=term,
        )

    return render(request, 'dashboard/people.html', {
        'page': 'people',
        'rows': rows[:PAGE_SIZE * 4],
        'total': rows.count(),
        'q': term,
        'practitioners': ExpertProfile.objects.count(),
        'members': Membership.objects.filter(state__in=('trial', 'active')).count(),
    })


# --------------------------------------------------------------- small parts

def _trend(now_count, before_count):
    """A percentage change, or None when there is nothing to compare to.

    Deliberately None rather than 0 for a first week: "0%" reads as "no
    growth", which is a different claim from "no history yet".
    """
    if not before_count:
        return None
    return round((now_count - before_count) * 100.0 / before_count)


def _signups_by_day(signups, now, days=14):
    """A fortnight of daily counts, scaled for the bar chart.

    Scaled here rather than in the template: a template that does arithmetic
    is a template nobody can read.
    """
    start = (now - datetime.timedelta(days=days - 1)).date()
    counts = {}
    for row in signups.filter(created_at__date__gte=start):
        day = timezone.localtime(row.created_at).date()
        counts[day] = counts.get(day, 0) + 1

    highest = max(counts.values()) if counts else 0
    bars = []
    for offset in range(days):
        day = start + datetime.timedelta(days=offset)
        count = counts.get(day, 0)
        bars.append({
            'day': day,
            'count': count,
            # A day with nothing still shows a sliver, so the row reads as a
            # row of days rather than a gap.
            'height': int(count * 100 / highest) if highest else 0,
            'label': day.strftime('%-d %b'),
        })
    return bars
