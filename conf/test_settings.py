from .settings import *  # noqa

# Use local memory cache so throttles don't persist across test runs
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Match production middleware ordering — insert at position 0
MIDDLEWARE.insert(0, "conf.test_utils.RequestIDMiddleware")  # noqa

# Use mock filter that provides all JSON formatter fields
LOGGING["filters"]["request_id"]["()"] = "conf.test_utils.RequestIDFilter"  # noqa
LOGGING["loggers"]["apps"]["level"] = "DEBUG"  # noqa

# Relax throttling for tests
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["user_login"] = "1000/minute"  # noqa
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["user"] = "1000/minute"  # noqa
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["anon"] = "1000/minute"  # noqa

# settings.py enables HTTPS redirect whenever DEBUG is off; the test client
# speaks plain HTTP, so without this every endpoint test 301-redirects to https.
SECURE_SSL_REDIRECT = False
