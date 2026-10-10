from django.contrib import admin

from .models import KycResponse, Profile


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'email', 'role', 'language', 'created_at')
    list_filter = ('role', 'language', 'created_at')
    search_fields = ('full_name', 'community_handle', 'user__email', 'user__username')
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(description='email', ordering='user__email')
    def email(self, obj):
        return obj.user.email


@admin.register(KycResponse)
class KycResponseAdmin(admin.ModelAdmin):
    list_display = ('user', 'answered', 'completed_at', 'updated_at')
    list_filter = ('completed_at',)
    search_fields = ('user__email', 'user__username')
    readonly_fields = ('updated_at',)

    @admin.display(description='questions answered')
    def answered(self, obj):
        return len(obj.answers or {})
