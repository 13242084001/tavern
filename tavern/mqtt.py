import logging
import time
import functools

try:
    from queue import Queue, Full, Empty
except ImportError:
    from Queue import Queue, Full, Empty

from paho.mqtt.client import Client

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

        # Arbitrary number, could just be 1 and only accept 1 message per stages
        # but we might want to raise an error if more than 1 message is received
        # during a test stage.
        self._message_queue = Queue(maxsize=10)
        self._userdata = {
            "queue": self._message_queue,
        }
        self._client.user_data_set(self._userdata)

    @staticmethod
    def _on_message(client, userdata, message):
        """Add any messages received to the queue

        Todo:
            If the queue is faull trigger an error in main thread somehow
        """
        try:
            userdata["queue"].put(message)
        except Full:
            logger.exception("message queue full")

    def message_received(self, topic, payload, timeout=1):
        """Check that a message is in the message queue

        Args:
            topic (str): topic message should have been on
            payload (str, dict): expected payload - can be a str or a dict...?
            timeout (int): How long to wait before signalling that the message
                was not received.

        Returns:
            bool: whether the message was received within the timeout

        Todo:
            Allow regexes for topic names? Better validation for mqtt payloads
        """

        time_spent = 0

        while time_spent < timeout:
            t1 = time.time()
            try:
                msg = self._message_queue.get(block=True, timeout=timeout)
            except Empty:
                time_spent += timeout
            else:
                time_spent += time.time() - t1
                if msg.payload != payload and msg.topic != topic:
                    # TODO
                    # Error?
                    logger.warning("Got unexpected message in '%s' with payload '%s'",
                        msg.topic, msg.payload)
                else:
                    logger.warning("Got expected message in '%s' with payload '%s'",
                        msg.topic, msg.payload)

                    return True

        logger.error("Message not received in time")
        return False

    def __enter__(self):
        self._client.connect_async(**self._connect_args)
        self._client.loop_start()

        return self

    def __exit__(self, *args):
        self._client.loop_stop()

    # TODO
    # collect message received - have a queue that collects messages with
    # on_message callback and then have a expected_message method which checks
    # that a certain message was received. Also need a clear_queue or something
    # to run at the beginning of each stage to clear this queue.


class MQTTRequest(object):
    """Wrapper for a single mqtt request on a client

    Similar to TRequest, publishes a single message.
    """

    def __init__(self, client, mqtt_block_config):
        expected = {
            "topic",
            "payload",
            "qos",
            # TODO retain?
        }

        check_expected_keys(expected, mqtt_block_config)

        self._prepared = functools.partial(client.publish, **mqtt_block_config)

    def run(self):
        return self._prepared()
