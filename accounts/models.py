"""Who is using the app.

Django's own User carries the credentials; everything the Flutter app needs
beyond them lives on Profile. That split is deliberate: this project already
has a live database with auth.User rows behind the landing site, and swapping
in a custom user model now would mean rebuilding it.

Email is the identifier the app signs in with. Django's User still needs a
username, so `Profile.create_user` fills it from the email -- callers never
deal with it.
"""

import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    """Mirrors the app's UserRole enum; the keys must match its `key` values."""

    USER = 'user', _('User')
    PRACTITIONER = 'practitioner', _('Practitioner')


class Language(models.TextChoices):
    """The two the app offers, same codes as the website's LANGUAGES."""

    ENGLISH = 'en', _('English')
    PIDGIN = 'pcm', _('Pidgin')


def avatar_path(instance, filename):
    return 'avatars/{0}/{1}'.format(instance.user_id, filename)


class Profile(models.Model):
    """Everything about a person that is not their password.

    One row per user, created with the account, so no view has to cope with a
    signed-in user who has no profile.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile',
    )
    role = models.CharField(
        _('role'), max_length=20, choices=Role.choices, default=Role.USER,
    )
    full_name = models.CharField(_('full name'), max_length=120, blank=True)

    # The forum handle. The app is explicit that a community post shows this
    # and never the account name -- people write about their mental health
    # there. Unique so one handle means one person.
    community_handle = models.CharField(
        _('community handle'), max_length=40, blank=True, unique=True, null=True,
    )

    language = models.CharField(
        _('language'), max_length=10, choices=Language.choices, default=Language.ENGLISH,
    )
    avatar = models.ImageField(_('avatar'), upload_to=avatar_path, blank=True)

    # Which figure the mood screens draw. Taken from the KYC gender answer, so
    # it is a display choice rather than a demographic record.
    FIGURE_CHOICES = (('female', _('Female')), ('male', _('Male')))
    mood_figure = models.CharField(
        _('mood figure'), max_length=10, choices=FIGURE_CHOICES, default='female',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('profile')
        verbose_name_plural = _('profiles')

    def __str__(self):
        return self.full_name or self.user.get_username()

    @property
    def is_practitioner(self):
        return self.role == Role.PRACTITIONER

    @classmethod
    def create_user(cls, email, password, full_name='', role=Role.USER, language=Language.ENGLISH):
        """Register someone, and give them their profile in the same breath."""
        model = get_user_model()
        email = email.strip().lower()
        user = model.objects.create_user(
            username=cls._username_for(email),
            email=email,
            password=password,
        )
        # Django splits the name in two; the app collects one field, so the
        # canonical copy is on the profile and this is for the admin's benefit.
        parts = full_name.strip().split(None, 1)
        user.first_name = parts[0][:150] if parts else ''
        user.last_name = parts[1][:150] if len(parts) > 1 else ''
        user.save(update_fields=['first_name', 'last_name'])

        profile = cls.objects.create(
            user=user, full_name=full_name.strip(), role=role, language=language,
        )
        return profile

    @staticmethod
    def _username_for(email):
        """A username Django is happy with, derived from the email.

        Nothing reads it; it exists because auth.User requires one and it must
        stay unique. The email is unique already, and fits in 150 characters
        in every realistic case, so it is the natural choice.
        """
        return email[:150]


class PasswordResetCode(models.Model):
    """A short code emailed to someone who has forgotten their password.

    A code rather than a link: this is a phone app, and a link in an email
    has to find its way back into the app through a deep link that does not
    exist yet. Six digits can be typed from the mail app without leaving the
    screen that is asking for them.

    The code is stored hashed. A leaked database should not hand anyone a
    working reset for every account that asked for one in the last quarter
    of an hour.
    """

    LIFETIME_MINUTES = 15
    MAX_ATTEMPTS = 5

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reset_codes',
    )
    code_hash = models.CharField(_('code'), max_length=128)
    expires_at = models.DateTimeField(_('expires'))
    attempts = models.PositiveSmallIntegerField(_('tries'), default=0)
    used_at = models.DateTimeField(_('used'), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = _('password reset code')
        verbose_name_plural = _('password reset codes')
        indexes = [models.Index(fields=['user', '-created_at'])]

    def __str__(self):
        return 'reset for {0}'.format(self.user.get_username())

    @property
    def is_usable(self):
        if self.used_at is not None:
            return False
        if self.attempts >= self.MAX_ATTEMPTS:
            return False
        return self.expires_at > timezone.now()

    def matches(self, code):
        """Whether this is the code, counting the try either way.

        Counted before the comparison, so a wrong guess costs an attempt even
        if the process dies immediately afterwards.
        """
        self.attempts += 1
        self.save(update_fields=['attempts'])
        return check_password(code, self.code_hash)

    def spend(self):
        self.used_at = timezone.now()
        self.save(update_fields=['used_at'])

    @classmethod
    def issue(cls, user):
        """A fresh code for this person, replacing any they already had.

        Returns (row, plain code). The plain code exists only long enough to
        be put in an email; nothing stores it.
        """
        cls.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        code = '{0:06d}'.format(secrets.randbelow(1000000))
        row = cls.objects.create(
            user=user,
            code_hash=make_password(code),
            expires_at=timezone.now() + timezone.timedelta(minutes=cls.LIFETIME_MINUTES),
        )
        return row, code


class KycResponse(models.Model):
    """The onboarding questionnaire's answers.

    Stored exactly as the app's KycRepository describes them: keyed by question
    id, each holding the chosen option values -- one for a single-choice
    question, several for a multi-select. Keeping it as one JSON document
    rather than a row per answer means adding, reordering or retiring a
    question needs no migration, which is what that contract asks for.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='kyc',
    )
    answers = models.JSONField(_('answers'), default=dict, blank=True)
    completed_at = models.DateTimeField(_('completed at'), null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('KYC response')
        verbose_name_plural = _('KYC responses')

    def __str__(self):
        state = 'complete' if self.is_complete else 'in progress'
        return '{0} ({1})'.format(self.user.get_username(), state)

    @property
    def is_complete(self):
        return self.completed_at is not None

    def mark_complete(self):
        if self.completed_at is None:
            self.completed_at = timezone.now()
            self.save(update_fields=['completed_at', 'updated_at'])

    def answer_for(self, question_id):
        """The chosen values for one question, always a list."""
        value = self.answers.get(question_id, [])
        return list(value) if isinstance(value, (list, tuple, set)) else [value]
