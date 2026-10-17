"""Deleting an account.

What the app promises is "permanently erase your profile, saved classes and
active subscription", and that is what happens to everything personal: the
profile, the questionnaire, every mood check-in, every journal entry, the
notifications, the push devices, the avatar.

Two things are kept, and both are kept deliberately:

**What was paid.** Payments and payouts are the business's accounts. Deleting
them would leave money that arrived with nothing to say where it came from,
which is not ours to do and not legal to do. They stay, pointing at an
account with no name and no address on it.

**What a session was.** A booking involves two people. Erasing the client
side of a therapy session would take the practitioner's own record of their
working day with it, so the session stays and the person is anonymised. The
recording and its transcript are a separate matter and are deleted on their
own thirty-day clock, which this does not interfere with.

So the account is anonymised and closed rather than dropped from the
database. From the person's side the difference is invisible: they cannot
sign in, nothing of theirs is readable, and their name and address are gone.
"""

import logging
import secrets

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

#: What the anonymised account's address becomes. Unique per account, and
#: at a domain that cannot receive mail, so nothing can be sent to it by
#: accident later.
ERASED_DOMAIN = 'deleted.invalid'


@transaction.atomic
def delete_account(user):
    """Erase everything personal and close the account.

    Returns a dict of what was removed, for the log. Runs in one
    transaction: a half-deleted account is worse than either outcome.
    """
    from billing.models import Membership
    from notifications.models import Notification, Preferences, PushDevice
    from wellbeing.models import JournalEntry, MoodEntry

    removed = {}

    # What they wrote and recorded about themselves. Gone outright: these
    # are the whole reason somebody asks to be deleted.
    removed['journal'] = JournalEntry.objects.filter(user=user).delete()[0]
    removed['moods'] = MoodEntry.objects.filter(user=user).delete()[0]
    removed['notifications'] = Notification.objects.filter(user=user).delete()[0]
    removed['devices'] = PushDevice.objects.filter(user=user).delete()[0]
    Preferences.objects.filter(user=user).delete()

    # The questionnaire, and the profile with its portrait.
    kyc = getattr(user, 'kyc', None)
    if kyc is not None:
        kyc.delete()

    profile = getattr(user, 'profile', None)
    if profile is not None:
        if profile.avatar:
            # The file as well as the row: a portrait left in storage is
            # still a portrait of somebody who asked to be forgotten.
            profile.avatar.delete(save=False)
        profile.full_name = ''
        profile.community_handle = None
        profile.save(update_fields=['full_name', 'community_handle', 'avatar'])

    # Stop anything from renewing or being charged. The membership row
    # stays because the payments do.
    membership = getattr(user, 'membership', None)
    if membership is not None:
        membership.state = 'inactive'
        membership.auto_renew = False
        membership.cancelled_at = timezone.now()
        membership.card_brand = ''
        membership.card_last4 = ''
        membership.card_signature = ''
        membership.payment_method_id = ''
        membership.save()

    # The account itself: closed, and with nothing on it that names anyone.
    stamp = secrets.token_hex(8)
    user.is_active = False
    user.first_name = ''
    user.last_name = ''
    user.email = 'erased-{0}@{1}'.format(stamp, ERASED_DOMAIN)
    user.username = 'erased-{0}'.format(stamp)
    user.set_unusable_password()
    user.save()

    logger.info('account %s erased: %s', user.pk, removed)
    return removed
