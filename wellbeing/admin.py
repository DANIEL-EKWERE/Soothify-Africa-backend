"""Deliberately read-only.

Mood check-ins and journal notes are the most private thing here. Staff may
need to see that a row exists when someone asks for their data or its
deletion; nobody needs to edit one, and the journal's text is not listed.
"""

from django.contrib import admin

from .models import JournalEntry, MoodEntry


@admin.register(MoodEntry)
class MoodEntryAdmin(admin.ModelAdmin):
    list_display = ('user', 'recorded_on', 'level_key', 'score')
    list_filter = ('recorded_on',)
    search_fields = ('user__email',)
    readonly_fields = ('user', 'score', 'note', 'recorded_on', 'recorded_at')

    def has_add_permission(self, request):
        return False


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    # No body column: a list page should not put someone's notes on screen.
    list_display = ('user', 'created_at', 'length')
    list_filter = ('created_at',)
    search_fields = ('user__email',)
    readonly_fields = ('user', 'title', 'body', 'prompt', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    @admin.display(description='length')
    def length(self, obj):
        return '{0} characters'.format(len(obj.body))
