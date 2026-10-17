"""The practitioner application, as the website asks for it.

The same application the app sends through the API (see
ApplicationSerializer), and deliberately the same shape as the app's own
screens: an intro, three steps behind a progress bar, and the
acknowledgement card -- Figma `259:59132`, `259:59145`, `259:59179`,
`259:59198` and `259:59229`. Somebody who starts on the website and finishes
in the app should recognise where they are.

One form per step rather than one long one, because the steps are separate
requests: what is answered is kept in the session until the last step, which
is where the files arrive and the application is created.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Application, ApplicationDocument, Discipline, SessionLanguage

#: What a certificate or an ID may be, and how large.
#:
#: Ten megabytes is a photographed certificate with room to spare, and the
#: four types are what a phone or a scanner produces. Anything else is
#: refused here rather than at the storage layer, where the message would be
#: about MIME types instead of about what to do.
ALLOWED_UPLOADS = ('.pdf', '.jpg', '.jpeg', '.png')
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

#: The app's own labels for the three disciplines (`259:59145`). The server
#: stores "therapy"; the person reads "Licensed Therapist".
DISCIPLINE_LABELS = (
    ('therapy', _('Licensed Therapist')),
    ('yoga', _('Certified Yoga Instructor')),
    ('pilates', _('Certified Pilates Instructor')),
)

LANGUAGE_LABELS = (
    ('english', _('English')),
    ('pidgin', _('Pidgin English')),
)


def _check_upload(uploaded):
    name = (uploaded.name or '').lower()
    if not name.endswith(ALLOWED_UPLOADS):
        raise forms.ValidationError(_('Upload a PDF, JPG or PNG.'))
    if uploaded.size > MAX_UPLOAD_BYTES:
        raise forms.ValidationError(
            _('That file is %(size).1f MB. Keep it under %(limit)s MB.'),
            params={
                'size': uploaded.size / (1024 * 1024),
                'limit': MAX_UPLOAD_BYTES // (1024 * 1024),
            },
        )
    return uploaded


class ApplicationStepOneForm(forms.Form):
    """Who you are — the app's first step (`259:59145`).

    Two questions the app does not ask: the address and the phone number.
    It takes those from the account; here there is no account, and an
    application nobody can answer is no use to either side.
    """

    disciplines = forms.MultipleChoiceField(
        label=_('What are your primary areas of expertise?'),
        choices=DISCIPLINE_LABELS,
        widget=forms.CheckboxSelectMultiple,
        error_messages={'required': _('Choose at least one.')},
    )
    full_name = forms.CharField(
        label=_('What is your name?'),
        max_length=120,
        widget=forms.TextInput(attrs={'autocomplete': 'name'}),
    )
    email = forms.EmailField(
        label=_('What is your email address?'),
        widget=forms.EmailInput(attrs={
            'autocomplete': 'email', 'inputmode': 'email',
        }),
    )
    phone = forms.CharField(
        label=_('What is your phone number?'),
        max_length=40,
        required=False,
        widget=forms.TextInput(attrs={
            'autocomplete': 'tel', 'inputmode': 'tel',
        }),
    )
    experience_years = forms.IntegerField(
        label=_('What is your years of experience'),
        min_value=0,
        max_value=60,
        widget=forms.NumberInput(attrs={'inputmode': 'numeric'}),
    )
    bio = forms.CharField(
        label=_('Tell us briefly about yourself?'),
        required=False,
        widget=forms.Textarea(attrs={'rows': 5}),
    )
    portfolio_url = forms.URLField(
        label=_('Your professional portfolio link'),
        required=False,
        widget=forms.URLInput(attrs={'placeholder': 'https://'}),
    )

    def clean_email(self):
        # Lower-cased, so the same person reads as the same person when the
        # application is matched to an account later.
        email = self.cleaned_data['email'].strip().lower()
        if Application.objects.filter(
            email=email, status__in=('submitted', 'reviewing'),
        ).exists():
            raise forms.ValidationError(
                _('We already have an application from this address and we '
                  'are reading it. We will write to you.'),
            )
        return email


class ApplicationStepTwoForm(forms.Form):
    """What you can run a session in — the app's second step (`259:59179`)."""

    languages = forms.MultipleChoiceField(
        label=_('Which languages are you completely fluent in for client '
                'sessions?'),
        choices=LANGUAGE_LABELS,
        widget=forms.CheckboxSelectMultiple,
        error_messages={'required': _('Choose at least one.')},
    )


class ApplicationStepThreeForm(forms.Form):
    """Proof — the app's third step (`259:59198`).

    Both uploads carry an asterisk in the frame, so both are required: an
    application without them cannot be reviewed, and a half-application is
    work nobody can do.
    """

    licence_number = forms.CharField(
        label=_('License / Certification Number'),
        max_length=80,
        required=False,
    )
    certification = forms.FileField(
        label=_('Upload official certification, degree, or practicing '
                'license (PDF, JPG, or PNG)*'),
    )
    identity = forms.FileField(
        label=_('Government-issued photo ID (NIN, International Passport) '
                'for identity verification*'),
    )
    insurance_provider = forms.CharField(
        label=_('Liability/Practitioner Insurance'),
        max_length=120,
        required=False,
    )
    agreed = forms.BooleanField(
        label=_('Everything I have written here is true, and the documents '
                'are mine.'),
        error_messages={
            'required': _('Please confirm before sending your application.'),
        },
    )

    # Not a question, and not shown to anybody: a field styled out of sight
    # that only an automated submitter fills in. Cheaper than a captcha and
    # it asks nothing of the person applying.
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_certification(self):
        return _check_upload(self.cleaned_data['certification'])

    def clean_identity(self):
        return _check_upload(self.cleaned_data['identity'])

    def clean_website(self):
        if self.cleaned_data.get('website'):
            # Answered a question nobody was shown. Refused with the same
            # wording a person would see, so a bot learns nothing from it.
            raise forms.ValidationError(_('Something went wrong. Try again.'))
        return ''


def save_application(draft, files):
    """Everything the three steps gathered, as one application.

    `draft` is what the first two steps put in the session plus the third
    step's own fields; `files` are the two uploads. Saved together, because
    an application without its documents cannot be reviewed.
    """
    application = Application.objects.create(
        source='web',
        full_name=draft['full_name'],
        email=draft['email'],
        phone=draft.get('phone', ''),
        disciplines=list(draft.get('disciplines') or []),
        languages=list(draft.get('languages') or []),
        experience_years=draft.get('experience_years') or 0,
        bio=draft.get('bio', ''),
        portfolio_url=draft.get('portfolio_url', ''),
        licence_number=draft.get('licence_number', ''),
        insurance_provider=draft.get('insurance_provider', ''),
    )
    for kind in ('certification', 'identity'):
        ApplicationDocument.objects.create(
            application=application, kind=kind, file=files[kind],
        )
    return application
