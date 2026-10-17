from urllib.parse import quote, urlencode

from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import get_language, gettext, gettext_lazy as _

from api import jobs
from experts.emails import send_application_received, tell_the_team
from experts.forms import (
    MAX_UPLOAD_BYTES,
    ApplicationStepOneForm,
    ApplicationStepThreeForm,
    ApplicationStepTwoForm,
    save_application,
)
from experts.models import Application

from .emails import send_welcome_email
from .forms import WaitlistForm
from .models import REFERRAL_BOOST, WaitlistSignup

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


def _legal_page(request, page, title):
    # Each language has its own copy of the policy text (privacy_pcm.html and so
    # on); a language without one falls back to the English page.
    return render(request, [f'home/{page}_{get_language()}.html', f'home/{page}.html'], {
        'page_title': title,
        'last_updated': LEGAL_UPDATED,
    })


def privacy(request):
    return _legal_page(request, 'privacy', _('Privacy Policy'))


def terms(request):
    return _legal_page(request, 'terms', _('Terms of Use'))


# --- waitlist ---------------------------------------------------------------

_SESSION_SIGNUP = 'waitlist_signup'   # the visitor's own row, for the success page
_SESSION_REF = 'waitlist_ref'         # whose link brought them here


def waitlist(request):
    # Remember a friend's link for the whole visit, so the referral still
    # counts if they look around before signing up.
    ref = request.GET.get('ref', '').strip()
    if ref:
        request.session[_SESSION_REF] = ref[:12]

    if request.method == 'POST':
        form = WaitlistForm(request.POST)
        if form.is_valid():
            signup = form.save(language=get_language(),
                               referral_code=request.session.get(_SESSION_REF))
            request.session[_SESSION_SIGNUP] = signup.pk
            # Only for a brand-new signup: someone editing their answers should
            # not be welcomed twice.
            if getattr(signup, 'was_created', False):
                send_welcome_email(signup, request)
            return redirect('home:waitlist_done')
    else:
        form = WaitlistForm()
    return render(request, 'home/waitlist.html', {'form': form})


def waitlist_done(request):
    signup = WaitlistSignup.objects.filter(pk=request.session.get(_SESSION_SIGNUP)).first()
    if signup is None:
        return redirect('home:waitlist')

    share_url = f"{request.build_absolute_uri(reverse('home:waitlist'))}?ref={signup.referral_code}"
    message = gettext('I just joined the Soothify waitlist, a calmer way to find balance. Join me:')
    return render(request, 'home/waitlist_done.html', {
        'signup': signup,
        'position': signup.position(),
        'boost': REFERRAL_BOOST,
        'share_url': share_url,
        'share_display': share_url.split('://', 1)[-1],
        'share_x': 'https://x.com/intent/post?' + urlencode({'text': message, 'url': share_url}),
        'share_whatsapp': 'https://wa.me/?text=' + quote(f'{message} {share_url}'),
        'share_gmail': 'https://mail.google.com/mail/?' + urlencode({
            'view': 'cm', 'fs': '1',
            'su': gettext('Join me on the Soothify waitlist'),
            'body': f'{message} {share_url}',
        }),
    })


# --- practitioners ---------------------------------------------------------

_SESSION_DRAFT = 'practitioner_application'        # answers, between steps
_SESSION_APPLICATION = 'practitioner_application_id'   # the one just sent

#: The three steps, in the app's order: who you are, what you speak, proof.
APPLICATION_STEPS = (
    ApplicationStepOneForm,
    ApplicationStepTwoForm,
    ApplicationStepThreeForm,
)


def practitioner_apply(request):
    """The intro — Figma `259:59132`, the app's own first screen.

    A page of its own rather than the top of the form, as in the app: what
    is being asked for is a certificate and a government ID, and somebody
    should know that before the first question.
    """
    # Starting again starts again: a draft left from a previous visit would
    # quietly pre-fill a form the person thinks is blank.
    request.session.pop(_SESSION_DRAFT, None)
    return render(request, 'home/apply_intro.html')


def practitioner_apply_step(request, step):
    """One step of three — `259:59145`, `259:59179`, `259:59198`.

    The answers live in the session until the last step, which is where the
    two files arrive and the application is created. Sending the files
    earlier would mean holding somebody's passport scan in a half-finished
    application.
    """
    if step not in (1, 2, 3):
        return redirect('home:practitioner_apply')

    draft = request.session.get(_SESSION_DRAFT) or {}

    # No stepping ahead: step three with nothing behind it would create an
    # application with no name on it.
    if step > 1 and not draft.get('full_name'):
        return redirect('home:practitioner_apply_step', step=1)
    if step > 2 and not draft.get('languages'):
        return redirect('home:practitioner_apply_step', step=2)

    form_class = APPLICATION_STEPS[step - 1]

    if request.method == 'POST':
        form = form_class(request.POST, request.FILES)
        if form.is_valid():
            answers = dict(form.cleaned_data)
            # Neither of these belongs in the session: the files are files,
            # and the honeypot is not an answer.
            files = {
                'certification': answers.pop('certification', None),
                'identity': answers.pop('identity', None),
            }
            answers.pop('website', None)
            answers.pop('agreed', None)

            draft.update(answers)

            if step < len(APPLICATION_STEPS):
                request.session[_SESSION_DRAFT] = draft
                return redirect('home:practitioner_apply_step', step=step + 1)

            application = save_application(draft, files)
            request.session.pop(_SESSION_DRAFT, None)
            request.session[_SESSION_APPLICATION] = application.pk
            # After the row is saved, and never allowed to undo it: somebody
            # who sent their certificate and got an error instead of an
            # acknowledgement would send it again.
            jobs.run(send_application_received, application.pk)
            tell_the_team(application)
            return redirect('home:practitioner_apply_done')
    else:
        form = form_class(initial=draft)

    return render(request, 'home/apply_step.html', {
        'form': form,
        'step': step,
        'total_steps': len(APPLICATION_STEPS),
        'max_upload_mb': MAX_UPLOAD_BYTES // (1024 * 1024),
        'is_last': step == len(APPLICATION_STEPS),
    })


def practitioner_apply_done(request):
    """The acknowledgement — `259:59229`, in the app's own words."""
    application = Application.objects.filter(
        pk=request.session.get(_SESSION_APPLICATION),
    ).first()
    if application is None:
        return redirect('home:practitioner_apply')

    return render(request, 'home/apply_done.html', {'application': application})
