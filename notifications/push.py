"""Getting a notification onto the phone.

The feed row and the push are two halves of one message: the row is what the
app shows when somebody opens it, the push is what the phone draws when they
do not. `notify()` writes both from one call, so the two can never say
different things -- the bug you get when a reminder is pushed from one place
and filed from another.

Nothing here is load-bearing. No key file on the server, Firebase down, a
token that expired three months ago: all of them end the same way -- the
feed row is written, the push is not, and the person reads it next time
they open the app. A notification is never worth failing a request or a
cron run for.
"""

import logging
import threading

from django.conf import settings

from api import jobs

logger = logging.getLogger(__name__)

# firebase_admin keeps its initialised app in a global of its own, and
# initialising it twice raises. This guards the first call; after it, the
# module-level cache answers without a lock.
_lock = threading.Lock()
_messaging_module = None
_looked = False


def _messaging():
    """`firebase_admin.messaging`, or None if this machine cannot push.

    Three ways to get None, all of them normal: the packages are not
    installed (a cPanel deployment that does not want push), no credentials
    file is configured, or the file is not where it says. Each is logged
    once -- a cron that pushes nothing should say so in the log the first
    time, not on every row it writes.
    """
    global _messaging_module, _looked

    if _looked:
        return _messaging_module

    with _lock:
        if _looked:                      # won by another thread while waiting
            return _messaging_module
        _looked = True

        path = getattr(settings, 'FIREBASE_CREDENTIALS', '')
        if not path:
            logger.info('push: no FIREBASE_CREDENTIALS set; pushes are off')
            return None

        try:
            import firebase_admin
            from firebase_admin import credentials, messaging
        except ImportError:
            logger.info('push: firebase-admin is not installed; pushes are off')
            return None

        try:
            # Reuses the app if something else (a management command, a
            # second worker thread) got there first.
            try:
                firebase_admin.get_app()
            except ValueError:
                firebase_admin.initialize_app(credentials.Certificate(path))
        except Exception:
            logger.exception('push: could not read the Firebase key at %s', path)
            return None

        _messaging_module = messaging
        return _messaging_module


# ----------------------------------------------------------- what to open
#
# The app turns `data['type']` into a route (see push_message.dart). It
# knows six: session, breathe, daily, article, mood, feed -- and anything
# else opens the feed, which is the one screen that can show any message.
# So an unmapped target is not a failure, it is the default.

def _data_for(notification):
    """The flat string map the app parses. FCM allows nothing else."""
    target = (notification.target or '').strip()

    if target.startswith('session/'):
        kind = {'type': 'session', 'session': target.split('/', 1)[1]}
    elif target in ('breathe', 'mood'):
        kind = {'type': target}
    else:
        kind = {'type': 'feed'}

    return dict(
        kind,
        # Both carried in `data` as well as in the notification block: the
        # app's background isolate draws a data-only push itself, and reads
        # them from here when it does.
        title=notification.title or notification.actor or 'Soothify',
        body=notification.body or notification.text or '',
        # So a tap can mark the row read, and so a future version of the app
        # can route on something this map does not yet carry.
        notification_id=str(notification.pk),
        target=target,
    )


# ------------------------------------------------------------- sending it

@jobs.task
def push_notification(notification_id):
    """Push one feed row to every phone its owner has.

    Runs inline on cPanel and in the worker on AWS. Returns how many phones
    took it, which is what the tests assert on.
    """
    from .models import Notification, PushDevice

    messaging = _messaging()
    if messaging is None:
        return 0

    notification = Notification.objects.filter(pk=notification_id).first()
    if notification is None:
        # Deleted between the row being written and this running. Nothing
        # to say, and nobody to say it to.
        return 0

    tokens = list(
        PushDevice.objects
        .filter(user_id=notification.user_id, is_active=True)
        .values_list('token', flat=True)
    )
    if not tokens:
        return 0

    data = _data_for(notification)
    message = messaging.MulticastMessage(
        tokens=tokens,
        # A notification block as well as the data: with it the system
        # draws the message itself, including when the app has been killed,
        # which a data-only push cannot rely on at all on iOS. The app's
        # background handler returns early when it sees one, so it is drawn
        # once rather than twice.
        notification=messaging.Notification(
            title=data['title'], body=data['body'],
        ),
        data=data,
        android=messaging.AndroidConfig(
            priority='high',
            notification=messaging.AndroidNotification(
                # Declared in the app at startup. Naming a channel that does
                # not exist means Android shows the push silently.
                channel_id='high_importance_channel',
                icon='ic_notification',
            ),
        ),
        apns=messaging.APNSConfig(
            payload=messaging.APNSPayload(
                aps=messaging.Aps(sound='default'),
            ),
        ),
    )

    try:
        batch = messaging.send_each_for_multicast(message)
    except Exception:
        logger.exception('push: sending notification %s failed', notification_id)
        return 0

    _forget_dead_tokens(messaging, tokens, batch.responses)
    logger.info(
        'push: notification %s to %s of %s devices',
        notification_id, batch.success_count, len(tokens),
    )
    return batch.success_count


def _permanent_errors(messaging):
    """The failures that mean "this token will never work again"."""
    dead = [messaging.UnregisteredError, messaging.SenderIdMismatchError]
    try:
        from firebase_admin.exceptions import InvalidArgumentError
    except ImportError:
        # Only reachable with a stand-in messaging module, i.e. in a test.
        pass
    else:
        # A token that is not merely gone but malformed.
        dead.append(InvalidArgumentError)
    return tuple(dead)


def _forget_dead_tokens(messaging, tokens, responses):
    """Delete the tokens Firebase says will never work again.

    An uninstalled app, or one whose token was reissued, answers
    UNREGISTERED for ever. Keeping those rows means every later push is
    partly addressed to phones that no longer exist, and the failure count
    in the log stops meaning anything.

    A plain send failure is not that -- the network, a rate limit, Firebase
    itself -- and those tokens are left alone to be tried again.

    `responses` comes back in the order the tokens went out; that pairing is
    the only thing saying which token failed.
    """
    from .models import PushDevice

    permanent = _permanent_errors(messaging)

    dead = []
    for token, response in zip(tokens, responses):
        error = getattr(response, 'exception', None)
        if error is not None and isinstance(error, permanent):
            dead.append(token)

    if dead:
        PushDevice.objects.filter(token__in=dead).delete()
        logger.info('push: dropped %s dead token(s)', len(dead))


# -------------------------------------------------------- the one entry point

def notify(user, *, filters=(), push=True, **fields):
    """File a notification in the feed and push it to the phone.

    Every place that tells somebody something goes through here, so the two
    halves are written from the same words. `push=False` is for rows that
    are worth having in the feed but not worth lighting a screen for.
    """
    from .models import Notification

    notification = Notification(user=user, **fields)
    if filters:
        notification.set_filters(list(filters))
    notification.save()

    if push:
        jobs.run(push_notification, notification.pk)
    return notification
