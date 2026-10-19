"""The upload form, and the few things staff edit by hand.

Everything in the app's library arrives through here: audio, video, articles,
cover art. Django's own admin can do this, but it asks for twenty fields in
database order and says nothing about which matter -- this asks for what a
piece of content actually needs, in the order someone thinks about it.
"""

from django import forms
from django.utils.translation import gettext_lazy as _

from content.models import Category, MediaItem, MediaType, Shelf


class MediaItemForm(forms.ModelForm):
    """Add or edit one piece of content."""

    shelves = forms.ModelMultipleChoiceField(
        queryset=Shelf.objects.filter(is_active=True),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=_('Which rows this shows up in.'),
    )

    class Meta:
        model = MediaItem
        fields = (
            'title', 'subtitle', 'tag', 'description',
            'media_type', 'duration_seconds',
            'cover', 'media_file', 'external_url',
            'category', 'credited_name',
            'is_free', 'is_featured', 'is_recommended', 'is_published',
        )
        widgets = {
            'title': forms.TextInput(attrs={'placeholder': _('Daily focus')}),
            'subtitle': forms.TextInput(attrs={'placeholder': _('Burnout relief')}),
            'tag': forms.TextInput(attrs={'placeholder': _('Pilates & Core')}),
            'description': forms.Textarea(attrs={'rows': 4}),
            'credited_name': forms.TextInput(attrs={'placeholder': _('Jacob Samuel')}),
            'external_url': forms.URLInput(
                attrs={'placeholder': _('https://… (only if the file is hosted elsewhere)')},
            ),
        }
        labels = {
            'media_type': _('Kind'),
            'duration_seconds': _('Length in seconds'),
            'cover': _('Cover art'),
            'media_file': _('The file'),
            'credited_name': _('Credited to'),
            'is_free': _('Free to play'),
            'is_featured': _("Show in Home's row"),
            'is_recommended': _('Show on the recommendations page'),
            'is_published': _('Published'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = Category.objects.filter(is_active=True)
        self.fields['category'].empty_label = _('No category')
        if self.instance.pk:
            self.fields['shelves'].initial = self.instance.shelves.all()

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get('media_type')
        has_file = bool(cleaned.get('media_file') or self.instance.media_file)
        has_url = bool(cleaned.get('external_url'))

        # An article is words, not a file. Everything else has to be playable
        # from somewhere, or the card leads to silence.
        if kind != MediaType.ARTICLE and not (has_file or has_url):
            raise forms.ValidationError(
                _('Upload a file, or give the address of one hosted elsewhere.'),
            )
        if kind == MediaType.ARTICLE and not cleaned.get('description'):
            raise forms.ValidationError(
                {'description': _('An article needs its text here.')},
            )
        return cleaned

    def save(self, commit=True):
        item = super().save(commit=commit)
        if commit:
            self._save_shelves(item)
        return item

    def _save_shelves(self, item):
        from content.models import ShelfItem

        chosen = self.cleaned_data.get('shelves') or []
        ShelfItem.objects.filter(item=item).exclude(shelf__in=chosen).delete()
        for shelf in chosen:
            ShelfItem.objects.get_or_create(
                shelf=shelf, item=item,
                defaults={'position': shelf.shelfitem_set.count()},
            )


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ('id', 'name', 'description', 'cover', 'position', 'is_active')
        labels = {'id': _('Short id (used by the app)')}


class BroadcastForm(forms.ModelForm):
    """Write one message to send out.

    The people field takes addresses rather than a multi-select: a select
    box listing every account is unusable at four accounts and unreadable at
    four thousand, and the thing staff actually have to hand is somebody's
    email.
    """

    addresses = forms.CharField(
        label=_('Send to these addresses'),
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 3,
            'placeholder': 'ada@example.com, bola@example.com',
        }),
        help_text=_('One per line, or separated by commas. Only used when '
                    'the audience is "the people picked below".'),
    )

    class Meta:
        from notifications.models import Broadcast

        model = Broadcast
        fields = ('title', 'body', 'channel', 'audience', 'is_marketing')
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': _('Something we think you will like'),
            }),
            'body': forms.Textarea(attrs={
                'rows': 10,
                'placeholder': _('Leave a blank line between paragraphs.'),
            }),
        }
        labels = {
            'title': _('Subject (and the push title)'),
            'body': _('Message'),
        }
        help_texts = {
            'body': _('Plain text. A blank line starts a new paragraph, and '
                      'nothing here is read as HTML.'),
        }

    def clean(self):
        from notifications.models import Broadcast

        cleaned = super().clean()
        audience = cleaned.get('audience')
        channel = cleaned.get('channel')

        if audience == Broadcast.Audience.WAITLIST and channel != Broadcast.Channel.EMAIL:
            # They have no account, so there is no feed to file a row in and
            # no phone registered to push to.
            raise forms.ValidationError({
                'channel': _('Waitlist signups have no account, so they can '
                             'only be emailed.'),
            })

        if audience == Broadcast.Audience.CHOSEN:
            self.chosen = self._resolve(cleaned.get('addresses') or '')
        else:
            self.chosen = []
        return cleaned

    def _resolve(self, raw):
        """Turn the typed addresses into accounts, saying which are unknown."""
        from django.contrib.auth import get_user_model

        wanted = [
            part.strip().lower()
            for part in raw.replace(',', '\n').splitlines()
            if part.strip()
        ]
        if not wanted:
            raise forms.ValidationError({
                'addresses': _('Give at least one address, or pick a different '
                               'audience.'),
            })

        found = list(get_user_model().objects.filter(email__in=wanted))
        missing = sorted(set(wanted) - {u.email.lower() for u in found})
        if missing:
            # Named rather than counted: "3 unknown" leaves staff to find
            # which three, and the usual cause is a typo in one of them.
            raise forms.ValidationError({
                'addresses': _('No account for: %(list)s') % {
                    'list': ', '.join(missing),
                },
            })
        return found
