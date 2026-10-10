"""Shapes taken from MoodEntry.fromJson and JournalEntry.fromJson."""

from rest_framework import serializers

from .models import JournalEntry, MoodEntry


class MoodEntrySerializer(serializers.ModelSerializer):
    # Derived, never stored: the app draws its own label and copy from this.
    level = serializers.CharField(source='level_key', read_only=True)

    class Meta:
        model = MoodEntry
        fields = ('id', 'score', 'level', 'note', 'recorded_on', 'recorded_at')
        read_only_fields = ('id', 'recorded_on', 'recorded_at')

    def validate_score(self, value):
        if not 0.0 <= value <= 1.0:
            raise serializers.ValidationError(
                'The slider runs from 0 (Awful) to 1 (Awesome).',
            )
        return value


class JournalEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = JournalEntry
        fields = ('id', 'title', 'body', 'prompt', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError('An empty note is not worth keeping.')
        return value


class ProfileStatsSerializer(serializers.Serializer):
    """The three counters on the Profile "My stats" card.

    A new account's true state is zeros, which is also what the design shows.
    """

    meditation_minutes = serializers.IntegerField()
    balance_minutes = serializers.IntegerField()
    live_sessions = serializers.IntegerField()
