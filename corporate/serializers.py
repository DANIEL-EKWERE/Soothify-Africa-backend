from rest_framework import serializers

from .models import Enquiry


class EnquirySerializer(serializers.ModelSerializer):
    class Meta:
        model = Enquiry
        fields = (
            'id', 'company', 'contact_name', 'email', 'phone', 'employees',
            'services', 'notes', 'created_at',
        )
        read_only_fields = ('id', 'created_at')

    def validate(self, attrs):
        # The form fills every box with sample text as a placeholder; an
        # untouched field must not arrive as "Landmark Enterprise".
        blank = [
            field for field in ('company', 'contact_name', 'email', 'phone')
            if not str(attrs.get(field, '')).strip()
        ]
        if blank:
            raise serializers.ValidationError(
                {field: 'This is needed to answer the enquiry.' for field in blank},
            )
        return attrs
