from django import forms
from django.utils.translation import gettext_lazy as _

from .models import WaitlistSignup


class WaitlistForm(forms.ModelForm):
    """The sign-up form behind every "Join waitlist" button."""

    goals = forms.MultipleChoiceField(
        choices=WaitlistSignup.Goal.choices, required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    interests = forms.MultipleChoiceField(
        choices=WaitlistSignup.Interest.choices, required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = WaitlistSignup
        fields = (
            'name', 'email', 'phone', 'goals', 'goals_other',
            'interests', 'interests_other', 'source', 'wants_updates',
        )
        widgets = {
            'name': forms.TextInput(attrs={'autocomplete': 'name', 'placeholder': _('Enter your name')}),
            'email': forms.EmailInput(attrs={'autocomplete': 'email', 'inputmode': 'email',
                                             'placeholder': _('Enter your email')}),
            'phone': forms.TextInput(attrs={'autocomplete': 'tel', 'inputmode': 'tel',
                                            'placeholder': _('Enter your phone number')}),
            'goals_other': forms.TextInput(attrs={'aria-label': _('Other reason')}),
            'interests_other': forms.TextInput(attrs={'aria-label': _('Other experience')}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['source'].choices = [('', _('Please select'))] + list(WaitlistSignup.Source.choices)

    def clean_email(self):
        # Stored lower-cased so the same person cannot take two spots.
        return self.cleaned_data['email'].strip().lower()

    def clean(self):
        data = super().clean()
        # Typing in an "Other" line counts as ticking its box.
        for choices_field, other_field in (('goals', 'goals_other'), ('interests', 'interests_other')):
            if data.get(other_field, '').strip():
                picked = data.get(choices_field) or []
                if 'other' not in picked:
                    data[choices_field] = [*picked, 'other']
        return data

    def validate_unique(self):
        # Signing up again with the same address updates that row (see save),
        # so the model's unique check must not reject it here.
        pass

    def save(self, commit=True, language='', referral_code=None):
        """One row per email: a repeat sign-up refreshes the answers but keeps
        the original place in line and referral link."""
        data = {k: v for k, v in self.cleaned_data.items() if k != 'email'}
        if language:
            data['language'] = language
        create_only = {}
        if referral_code:
            referrer = WaitlistSignup.objects.filter(referral_code=referral_code).first()
            if referrer and referrer.email != self.cleaned_data['email']:
                create_only['referred_by'] = referrer
        signup, _created = WaitlistSignup.objects.update_or_create(
            email=self.cleaned_data['email'],
            defaults=data,
            create_defaults={**data, **create_only},
        )
        return signup
