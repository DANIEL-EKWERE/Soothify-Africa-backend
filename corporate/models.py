"""Companies asking about workplace wellness.

The Plans screen's Corporate card routes to a conversation rather than a
checkout, so this is a lead to be answered by a person, not an order.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Enquiry(models.Model):
    """One submission of the Corporate form.

    The four required fields are the ones without which the enquiry cannot be
    answered; the design marks none of them, which is a gap in the design
    rather than a decision.
    """

    STATUS = (
        ('new', _('New')),
        ('contacted', _('Contacted')),
        ('won', _('Became a client')),
        ('closed', _('Closed')),
    )

    company = models.CharField(_('company'), max_length=160)
    contact_name = models.CharField(_('contact'), max_length=120)
    email = models.EmailField(_('email'))
    phone = models.CharField(_('phone'), max_length=40)

    # Free text on purpose: the form offers a hint ("15"), not a range, and a
    # company that writes "about 40, growing" has told us something useful.
    employees = models.CharField(_('employees'), max_length=60, blank=True)
    services = models.CharField(_('preferred services'), max_length=300, blank=True)
    notes = models.TextField(_('additional requirements'), blank=True)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='corporate_enquiries',
    )
    status = models.CharField(_('status'), max_length=20, choices=STATUS, default='new')
    handled_at = models.DateTimeField(_('answered at'), null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ('-created_at',)
        verbose_name = _('corporate enquiry')
        verbose_name_plural = _('corporate enquiries')
        indexes = [models.Index(fields=['status', '-created_at'])]

    def __str__(self):
        return '{0} ({1})'.format(self.company, self.get_status_display())
