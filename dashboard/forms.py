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
