import logging

from paho.mqtt import Client

from .util.keys import check_expected_keys
from .util import exceptions


logger = logging.getLogger(__name__)


class MQTTClient(object):
    
    def __init__(self, **kwargs):
        expected_main = {
            "client",
            "tls",
            "connect",
        }

        expected_blocks = {
            "client": {
                "client_id",
                "clean_session",
                "userdata",
                "protocol",
                "transport",
            },
            "connect": {
                "host",
                "port",
                "keepalive",
            },
            "tls": {
                "enable",
                # TODO custom ca certs etc.
            }
        }

        # check main block first
        check_expected_keys(expected_main, kwargs)

        # then check constructor/connect/tls_set args
        self._client_args = kwargs.pop("client", {})
        check_expected_keys(expected_main, self._client_args)

        self._connect_args = kwargs.pop("connect", {})
        check_expected_keys(expected_blocks["connect"], self._connect_args)

        if "host" not in self._connect_args:
            msg = "Need 'host' in 'connect' block for mqtt"
            logger.error(msg)
            raise exceptions.MissingKeysError(msg)

        # If there is any tls kwarg (including 'enable'), enable tls
        self._tls_args = kwargs.pop("tls", {})
        self._enable_tls = bool(self._tls_args)
        # don't want to pass this through to tls_set
        self._tls_args.pop("enable", None)

        self._client = Client(**self._client_args)

        if self._enable_tls:
            self._client.tls_set(**self._tls_args)

    def __enter__(self):
        self._client.connect_async(**self._connect_args)
        self._client.loop_start()

        return self

    def __exit__(self, *args):
        self._client.loop_stop()
