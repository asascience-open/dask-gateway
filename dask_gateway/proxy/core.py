import os
import json
import subprocess
import uuid
from urllib.parse import urlparse

from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from traitlets.config import LoggingConfigurable
from traitlets import default, Unicode, CaselessStrEnum

from ..utils import random_port

_PROXY_EXE = os.path.join(
    os.path.abspath(os.path.dirname(os.path.relpath(__file__))),
    'configurable-tls-proxy')


class Proxy(LoggingConfigurable):
    """A proxy for connecting Dask clients to schedulers behind a firewall."""

    log_level = CaselessStrEnum(
        ["error", "warn", "info", "debug"],
        default_value="warn",
        help="The proxy log-level.",
        config=True
    )

    public_url = Unicode(
        "tls://0.0.0.0:8080",
        help="""
        The public facing URL of the Proxy.

        This is the address that dask clients will connect to.
        """,
        config=True
    )

    api_url = Unicode(
        help="""
        The address for configuring the Proxy.

        This is the address that the Dask Gateway will connect to when
        adding/removing routes. This must be reachable from the Dask Gateway
        server, but shouldn't be publicly accessible (if possible). Default's
        to ``localhost:{random-port}``.
        """,
        config=True
    )

    auth_token = Unicode(
        help="""
        The Proxy auth token

        Loaded from the CONFIG_TLS_PROXY_TOKEN env variable by default.
        """,
        config=True
    )

    @default('api_url')
    def _default_api_url(self):
        return "http://localhost:%d" % random_port()

    @default('auth_token')
    def _auth_token_default(self):
        token = os.environ.get('CONFIG_TLS_PROXY_TOKEN', '')
        if not token:
            self.log.info("Generating new CONFIG_TLS_PROXY_TOKEN")
            token = uuid.uuid4().hex
        return token

    def start(self):
        """Start the proxy."""
        address = urlparse(self.public_url).netloc
        api_address = urlparse(self.api_url).netloc
        command = [_PROXY_EXE,
                   '-address', address,
                   '-api-address', api_address,
                   '-log-level', self.log_level,
                   '-is-child-process']

        env = os.environ.copy()
        env['CONFIG_TLS_PROXY_TOKEN'] = self.auth_token
        self.log.info("Starting the Dask Gateway Proxy...")
        proc = subprocess.Popen(command,
                                env=env,
                                stdin=subprocess.PIPE,
                                stdout=None,
                                stderr=None,
                                start_new_session=True)
        self.proxy_process = proc

    def stop(self):
        """Stop the proxy."""
        self.proxy_process.terminate()

    async def _api_request(self, url, method='GET', body=None):
        client = AsyncHTTPClient()
        if isinstance(body, dict):
            body = json.dumps(body)
        req = HTTPRequest(url,
                          method=method,
                          headers={'Authorization': 'token %s' % self.auth_token},
                          body=body)
        return await client.fetch(req)

    async def add_route(self, route, target):
        """Add a route to the proxy.

        Parameters
        ----------
        route : string
            The SNI route to add.
        target : string
            The ip:port to map this SNI route to.
        """
        self.log.debug("Adding route %s -> %s", route, target)
        await self._api_request(
            url='%s/api/routes/%s' % (self.api_url, route),
            method='PUT',
            body={'target': target}
        )

    async def delete_route(self, route):
        """Delete a route from the proxy.

        Idempotent, no error is raised if the route doesn't exist.

        Parameters
        ----------
        route : string
            The SNI route to delete.
        """
        self.log.debug("Removing route %s", route)
        await self._api_request(
            url='%s/api/routes/%s' % (self.api_url, route),
            method='DELETE'
        )

    async def get_all_routes(self):
        """Get the proxies current routing table.

        Returns
        -------
        routes : dict
            A dict of route -> target for all routes in the proxy.
        """
        resp = await self._api_request(
            url='%s/api/routes/' % self.api_url,
            method='GET'
        )
        return json.loads(resp.body.decode('utf8', 'replace'))