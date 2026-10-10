"""Who may do what.

The app has two audiences in one binary (`UserRole.user` and
`UserRole.practitioner`), so most rules come down to the role on the profile
plus "is this row yours".
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsPractitioner(BasePermission):
    """The expert side: dashboard, availability, earnings, payouts."""

    message = 'Only practitioners can use this.'

    def has_permission(self, request, view):
        profile = getattr(request.user, 'profile', None)
        return bool(profile and profile.role == 'practitioner')


class IsOwner(BasePermission):
    """The row belongs to the caller. Journal notes, mood entries, drafts."""

    message = 'This does not belong to you.'
    owner_field = 'user'

    def has_object_permission(self, request, view, obj):
        owner = getattr(obj, getattr(view, 'owner_field', self.owner_field), None)
        return owner == request.user


class IsOwnerOrReadOnly(BasePermission):
    """Anyone signed in may read; only the author may change or delete.

    Community threads and comments: the forum is shared, the post is not.
    """

    message = 'Only the author can change this.'

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return getattr(obj, 'author', None) == request.user
