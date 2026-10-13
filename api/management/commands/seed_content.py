"""Create the rows the app expects to find: plans, categories, shelves.

Taken from the app's own mock data and the Plans design, so the first run
against a real API looks like the screens do rather than empty.

What this does **not** create is content: no audio, no video, no cover art.
Those are files somebody has to upload through the admin. The shelves it
makes are empty on purpose -- an empty shelf is honest, and sample rows
pointing at nothing would look like a bug later.

    python manage.py seed_content
    python manage.py seed_content --reset     # put the copy back as written
"""

from django.core.management.base import BaseCommand

from billing.models import Plan
from content.models import Category, Shelf

# The five from MockContentRepository.
CATEGORIES = (
    ('calm', 'Calm', 'Wind down and settle', 0),
    ('sleep', 'Sleep', 'Drift off gently', 1),
    ('focus', 'Focus', 'Find your rhythm', 2),
    ('breathe', 'Breathwork', 'Guided breathing', 3),
    ('stories', 'Stories', 'Told by our voices', 4),
)

# The named rows the library screens fetch by key. Preview counts are what
# the design draws: three cards on most, two on the sound shelves.
SHELVES = (
    ('voice-overs', 'Voice overs', 3, 0),
    ('sound-effects', 'Sound effects', 2, 1),
    ('yoga-flow', 'Yoga flow', 3, 2),
    ('sounds', 'Sounds', 2, 3),
    ('videos-pilates', 'Pilates & Core', 3, 4),
    ('videos-yoga', 'Yoga', 3, 5),
)

# The three cards on Plans, as the designer redrew them. Two sell nothing:
# Passport collects an email, Corporate opens a conversation.
PLANS = (
    {
        'key': 'core-annual',
        'name': 'Soothify Core',
        'description': (
            'Your sanctuary for daily balance. Includes unlimited streaming '
            'of our video library, sleep stories, soothing soundscapes, and '
            'the wellness journal.'
        ),
        'price_naira': 150000,
        'price_suffix': '/year',
        'cadence': 'annual',
        'action': 'trial',
        'trial_days': 7,
        'position': 0,
    },
    {
        'key': 'core-monthly',
        'name': 'Soothify Core',
        'description': (
            'Your sanctuary for daily balance. Includes unlimited streaming '
            'of our video library, sleep stories, soothing soundscapes, and '
            'the wellness journal.'
        ),
        'price_naira': 15000,
        'price_suffix': '/month',
        'cadence': 'monthly',
        'action': 'trial',
        'trial_days': 7,
        'position': 1,
    },
    {
        'key': 'passport',
        'name': 'Soothify Passport',
        'description': (
            'Your city-wide pass to physical movement. Enjoy 6 curated class '
            'bookings monthly across boutique studios, gyms, and sanctuaries '
            'in Abuja and Lagos (fully inclusive of your Core digital '
            'access).'
        ),
        'action': 'waitlist',
        'is_recommended': True,
        'position': 2,
    },
    {
        'key': 'corporate',
        'name': 'Soothify Corporate Wellness',
        'description': (
            'Tailored holistic care designed to nurture your entire team. '
            'Bring mindful movement, stress relief, and restorative wellness '
            'directly into your workplace culture.'
        ),
        'action': 'corporate',
        'position': 3,
    },
)


class Command(BaseCommand):
    help = 'Create the plans, categories and shelves the app expects.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset', action='store_true',
            help='Overwrite the copy on rows that already exist.',
        )

    def handle(self, *args, **options):
        reset = options['reset']
        self.stdout.write('Categories: {0}'.format(self._categories(reset)))
        self.stdout.write('Shelves:    {0}'.format(self._shelves(reset)))
        self.stdout.write('Plans:      {0}'.format(self._plans(reset)))
        self.stdout.write('')
        self.stdout.write(
            'Now add media in the admin, and run setup_bachs_products once '
            'BACHS_SECRET_KEY is set.',
        )

    def _categories(self, reset):
        made = 0
        for key, name, description, position in CATEGORIES:
            _row, created = Category.objects.update_or_create(
                id=key,
                defaults={'name': name, 'description': description, 'position': position}
                if reset else {},
            )
            if created:
                Category.objects.filter(pk=key).update(
                    name=name, description=description, position=position,
                )
                made += 1
        return self._report(made, Category.objects.count())

    def _shelves(self, reset):
        made = 0
        for key, title, preview, position in SHELVES:
            defaults = {'title': title, 'preview_limit': preview, 'position': position}
            _row, created = Shelf.objects.update_or_create(
                key=key, defaults=defaults if reset else {},
            )
            if created:
                Shelf.objects.filter(pk=key).update(**defaults)
                made += 1
        return self._report(made, Shelf.objects.count())

    def _plans(self, reset):
        made = 0
        for plan in PLANS:
            key = plan['key']
            defaults = {field: value for field, value in plan.items() if field != 'key'}
            _row, created = Plan.objects.update_or_create(
                key=key,
                # Never overwrite bachs_product_id: it is set once the
                # product exists at Bachs, and losing it silently breaks
                # checkout for that card.
                defaults=defaults if reset else {},
            )
            if created:
                Plan.objects.filter(pk=key).update(**defaults)
                made += 1
        return self._report(made, Plan.objects.count())

    def _report(self, made, total):
        if made:
            return '{0} created, {1} in total'.format(made, total)
        return 'nothing to create, {0} in total'.format(total)
