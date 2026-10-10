"""Shapes taken from Discussion.fromJson and Comment.fromJson.

Both read `author_username`, so that is the key -- even though what it carries
is the forum handle, never the account's name or email.
"""

from rest_framework import serializers

from .models import Comment, Discussion, Report


class DiscussionSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source='author_handle', read_only=True)
    liked = serializers.SerializerMethodField()

    class Meta:
        model = Discussion
        fields = (
            'id', 'title', 'body', 'author_username', 'like_count',
            'comment_count', 'share_count', 'liked', 'created_at',
        )
        read_only_fields = (
            'id', 'like_count', 'comment_count', 'share_count', 'created_at',
        )

    def get_liked(self, obj):
        """Whether the caller has liked this, so the heart draws filled."""
        user = getattr(self.context.get('request'), 'user', None)
        if user is None or not user.is_authenticated:
            return False
        liked = self.context.get('liked_ids')
        if liked is not None:          # prefetched for a list; no query here
            return obj.pk in liked
        return obj.likes.filter(user=user).exists()

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError('Give the thread a title.')
        return value.strip()

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError('Write something before posting.')
        return value.strip()


class CommentSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source='author_handle', read_only=True)

    class Meta:
        model = Comment
        fields = ('id', 'author_username', 'body', 'created_at')
        read_only_fields = ('id', 'created_at')

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError('Write something before replying.')
        return value.strip()


class ReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = Report
        fields = ('id', 'reason', 'detail', 'created_at')
        read_only_fields = ('id', 'created_at')
