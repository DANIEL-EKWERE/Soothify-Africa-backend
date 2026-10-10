"""Moderation.

Posts are shown by handle, and removing one hides it rather than deleting it:
if a post is reported for encouraging self-harm, the text needs to still exist
when someone looks into it.
"""

from django.contrib import admin
from django.utils import timezone

from .models import Comment, Discussion, DiscussionLike, Report


@admin.register(Discussion)
class DiscussionAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'author_handle', 'comment_count', 'like_count',
        'is_removed', 'created_at',
    )
    list_filter = ('is_removed', 'created_at')
    search_fields = ('title', 'body', 'author_handle')
    readonly_fields = ('author', 'author_handle', 'created_at', 'updated_at')
    actions = ['remove_posts', 'restore_posts']

    @admin.action(description='Hide from the forum')
    def remove_posts(self, request, queryset):
        self.message_user(request, '{0} hidden.'.format(queryset.update(is_removed=True)))

    @admin.action(description='Put back in the forum')
    def restore_posts(self, request, queryset):
        self.message_user(request, '{0} restored.'.format(queryset.update(is_removed=False)))


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('author_handle', 'discussion', 'is_removed', 'created_at')
    list_filter = ('is_removed', 'created_at')
    search_fields = ('body', 'author_handle')
    readonly_fields = ('author', 'author_handle', 'discussion', 'created_at')


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ('reason', 'target', 'reporter', 'handled_at', 'created_at')
    list_filter = ('reason', 'handled_at', 'created_at')
    readonly_fields = ('discussion', 'comment', 'reporter', 'reason', 'detail', 'created_at')
    actions = ['mark_handled']

    @admin.display(description='about')
    def target(self, obj):
        return obj.discussion or obj.comment

    @admin.action(description='Mark as dealt with')
    def mark_handled(self, request, queryset):
        updated = queryset.update(handled_at=timezone.now())
        self.message_user(request, '{0} marked.'.format(updated))


admin.site.register(DiscussionLike)
