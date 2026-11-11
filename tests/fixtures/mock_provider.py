"""Scripted stand-ins for the HubSpot provider used across all layers.

The service under test talks to HubSpot through two seams:

* ``api.views.HubSpotDealsService``             - token validation on start
* ``api.services.extraction.HubSpotDealsService`` - the extraction worker

``ProviderScript`` produces a drop-in service class for both seams. Steps are
consumed in order by ``get_deals()``: a dict is returned as the provider page,
an exception instance is raised. Any request beyond the scripted steps fails
the test so over-fetching is caught deterministically.
"""

from api.services.hubspot import InvalidTokenError

from .extraction_data import make_page


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, status_code, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    """Scripted ``requests.Session`` returning canned responses in order.

    Steps may be ``FakeResponse`` objects or exceptions (raised to simulate
    network failures). An extra request beyond the scripted steps fails the
    test instead of hanging or succeeding silently.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params, "timeout": timeout})
        if not self.responses:
            raise AssertionError("FakeSession received an unexpected extra request")
        step = self.responses.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class ProviderScript:
    """Configurable replacement for ``HubSpotDealsService``.

    Usage::

        script = ProviderScript()
        script.add_page(deals)                  # provider returns one page
        script.add_error(HubSpotAPIError("...")) # provider then fails
        install_provider(script)                # fixture installs it

    ``on_request`` is an optional callable invoked with the request kwargs
    before a step is consumed - used to inject side effects mid-extraction
    (e.g. cancelling a job between provider pages).
    """

    def __init__(self, steps=()):
        self.steps = list(steps)
        self.calls = []
        self.tokens = []
        self.instantiations = 0
        self.on_request = None  # optional callable(request_kwargs) invoked before each step
        script = self

        class ScriptedService:
            def __init__(self, access_token, **kwargs):
                if not access_token:
                    raise InvalidTokenError("Invalid API token provided")
                script.instantiations += 1
                script.tokens.append(access_token)

            def get_deals(self, limit=None, after=None, properties=None, archived=False):
                script.calls.append(
                    {
                        "limit": limit,
                        "after": after,
                        "properties": properties,
                        "archived": archived,
                    }
                )
                if script.on_request is not None:
                    script.on_request(
                        {"limit": limit, "after": after, "properties": properties, "archived": archived}
                    )
                return script.next_response()

            def validate_token(self):
                self.get_deals(limit=1)
                return True

        self.service_class = ScriptedService

    def next_response(self):
        if not self.steps:
            raise AssertionError("ProviderScript received an unexpected request")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def add_page(self, results, after=None):
        self.steps.append(make_page(results, after=after))

    def add_error(self, exc):
        self.steps.append(exc)

    @property
    def remaining_steps(self):
        return len(self.steps)


class ProviderRouter:
    """Route each access token to its own ``ProviderScript``.

    Used by concurrency tests that run several jobs in parallel threads: all
    threads share one installed service class, but each token gets an
    isolated, deterministic script so results cannot bleed between jobs.
    """

    def __init__(self):
        self.scripts = {}
        router = self

        class RoutedService:
            def __init__(self, access_token, **kwargs):
                script = router.scripts.get(access_token)
                if script is None:
                    raise AssertionError(f"No ProviderScript registered for the given token")
                self._inner = script.service_class(access_token, **kwargs)

            def get_deals(self, **kwargs):
                return self._inner.get_deals(**kwargs)

            def validate_token(self):
                return self._inner.validate_token()

        self.service_class = RoutedService

    def register(self, token, script):
        self.scripts[token] = script
        return script
