"""Where the content team works.

The app has no upload path of its own -- everything in the library is put
there by staff, so this admin is the whole authoring tool.
"""

from django.contrib import admin

from .models import Category, MediaItem, MediaRating, PlaybackEvent, Shelf, ShelfItem


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'id', 'position', 'is_active', 'item_count')
    list_editable = ('position', 'is_active')
    search_fields = ('id', 'name')

    @admin.display(description='items')
    def item_count(self, obj):
        return obj.items.count()


class ShelfItemInline(admin.TabularInline):
    model = ShelfItem
    extra = 3
    autocomplete_fields = ('item',)
    ordering = ('position',)


@admin.register(Shelf)
class ShelfAdmin(admin.ModelAdmin):
    list_display = ('title', 'key', 'preview_limit', 'position', 'is_active')
    list_editable = ('preview_limit', 'position', 'is_active')
    search_fields = ('key', 'title')
    inlines = [ShelfItemInline]


@admin.register(MediaItem)
class MediaItemAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'media_type', 'category', 'practitioner_name', 'duration_minutes',
        'is_free', 'is_featured', 'is_published', 'rating_average',
    )
    list_filter = (
        'media_type', 'category', 'is_free', 'is_featured', 'is_recommended',
        'is_published',
    )
    list_editable = ('is_free', 'is_featured', 'is_published')
    search_fields = ('title', 'subtitle', 'credited_name', 'practitioner__full_name')
    readonly_fields = ('rating_average', 'rating_count', 'created_at', 'updated_at')
    autocomplete_fields = ('practitioner',)
    fieldsets = (
        (None, {'fields': ('title', 'subtitle', 'tag', 'description')}),
        ('Media', {
            'fields': ('media_type', 'duration_seconds', 'cover', 'media_file',
                       'external_url'),
            'description': 'Upload a file, or point at one already hosted '
                           'elsewhere. Either way the app only ever gets a '
                           'short-lived signed link.',
        }),
        ('Who and where', {'fields': ('category', 'practitioner', 'credited_name')}),
        ('Placement', {
            'fields': ('is_free', 'is_featured', 'is_recommended', 'position'),
        }),
        ('Publishing', {'fields': ('is_published', 'published_at')}),
        ('Scores', {'fields': ('rating_average', 'rating_count'), 'classes': ('collapse',)}),
    )

    @admin.display(description='length', ordering='duration_seconds')
    def duration_minutes(self, obj):
        return '{0} min'.format(round(obj.duration_seconds / 60))


@admin.register(PlaybackEvent)
class PlaybackEventAdmin(admin.ModelAdmin):
    list_display = ('user', 'item', 'seconds_played', 'completed', 'played_at')
    list_filter = ('completed', 'played_at')
    search_fields = ('user__email', 'item__title')
    readonly_fields = ('user', 'item', 'seconds_played', 'completed', 'played_at')


@admin.register(MediaRating)
class MediaRatingAdmin(admin.ModelAdmin):
    list_display = ('item', 'user', 'score', 'updated_at')
    list_filter = ('score',)
    search_fields = ('item__title', 'user__email')
