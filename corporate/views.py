"""/api/v1/corporate/ -- the enquiry form."""

from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle

from api import jobs

from .emails import send_enquiry_notice
from .models import Enquiry
from .serializers import EnquirySerializer


class EnquiryCreateView(generics.CreateAPIView):
    """Anyone may send one: a company's HR lead is not an app user.

    Rate-limited because it is an open form that sends mail.
    """

    serializer_class = EnquirySerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'
    queryset = Enquiry.objects.all()

    def perform_create(self, serializer):
        enquiry = serializer.save(
            user=self.request.user if self.request.user.is_authenticated else None,
        )
        # Saved first, told second: the enquiry survives a dead mail server.
        jobs.run(send_enquiry_notice, enquiry.pk)
