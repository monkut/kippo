from rest_framework.routers import SimpleRouter

from customers.viewsets import KippoCustomerViewSet

# SimpleRouter (not DefaultRouter) so this doesn't register a second "api-root" view —
# it's included alongside the projects router under the same /api/ prefix.
router = SimpleRouter()
router.register(r"customers", KippoCustomerViewSet, basename="kippocustomer")

# Mounted at /api/ in the root urlconf, keeping the public path /api/customers/ unchanged.
api_patterns = router.urls
