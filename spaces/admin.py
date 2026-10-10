from django.contrib import admin

from .models import PassportWaitlistEntry, WellnessSpace


@admin.register(WellnessSpace)
class WellnessSpaceAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'area', 'city', 'is_active', 'position')
    list_filter = ('category', 'city', 'is_active')
    list_editable = ('is_active', 'position')
    search_fields = ('name', 'area', 'city')
    fieldsets = (
        (None, {'fields': ('id', 'name', 'category', 'tags', 'about', 'photo')}),
        ('Where', {
            'fields': ('area', 'city', 'latitude', 'longitude'),
            'description': 'Coordinates drive the "2.4 km away" line and the '
                           'map pins. Without them a card simply omits the '
                           'distance.',
        }),
        ('Contact', {'fields': ('instagram', 'phone')}),
        ('Listing', {'fields': ('is_active', 'position')}),
    )


@admin.register(PassportWaitlistEntry)
class PassportWaitlistAdmin(admin.ModelAdmin):
    list_display = ('email', 'city', 'user', 'source', 'notified_at', 'created_at')
    list_filter = ('city', 'notified_at', 'created_at')
    search_fields = ('email', 'city')
    readonly_fields = ('user', 'created_at')
