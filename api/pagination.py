"""Paging shared by every list endpoint.

The Flutter app asks for pages by number -- `getDiscussions({int page = 1})`
-- so the page-number style is what the client already speaks.
"""

from rest_framework.pagination import PageNumberPagination


class PagePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100
