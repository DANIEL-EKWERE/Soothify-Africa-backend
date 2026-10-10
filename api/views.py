"""Endpoints that belong to no single domain."""

from django.conf import settings
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([AllowAny])
def health(request):
    """Is the API up, and which deployment answered?

    Deliberately says nothing a stranger could use: no versions, no hostnames.
    """
    return Response({'status': 'ok', 'deployment': settings.DEPLOY_TARGET})
