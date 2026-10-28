import json
import time
from uuid import uuid4

from django.db import connections

from nautobot.core import celery
from nautobot.core.celery.backends import NautobotDatabaseBackend
from nautobot.core.testing import TestCase, TransactionTestCase
from nautobot.dcim.models import Device
from nautobot.extras.choices import JobResultStatusChoices
from nautobot.extras.models import JobResult


class CeleryTest(TestCase):
    def test__dumps(self):
        self.assertEqual('"I am UTF-8! 😀"', celery._dumps("I am UTF-8! 😀"))

    def test__dumps_and__loads_handle_model_instances_but_warn(self):
        device = Device.objects.first()
        self.assertIsNotNone(device)
        with self.assertWarns(DeprecationWarning):
            json_data = celery._dumps(device)

        self.assertEqual(
            json_data,
            json.dumps(
                {
                    "id": str(device.id),
                    "__nautobot_type__": "nautobot.dcim.models.devices.Device",
                    "display": device.display,
                },
                ensure_ascii=False,
            ),
        )

        with self.assertWarns(DeprecationWarning):
            device_obj = celery._loads(json_data)

        self.assertEqual(device_obj.id, device.id)

    def test__dumps_tags(self):
        device = Device.objects.filter(tags__isnull=False).first()
        self.assertIsNotNone(device)
        with self.assertWarns(DeprecationWarning):
            json_data = celery._dumps(device.tags)

        self.assertEqual(
            json_data, json.dumps([str(val) for val in device.tags.values_list("id", flat=True)], ensure_ascii=False)
        )

    def test__loads_unknown_type(self):
        with self.assertRaises(TypeError):
            celery._loads(
                json.dumps(
                    {
                        "id": str(uuid4()),
                        "__nautobot_type__": "nautobot.invalid.module.path",
                        "display": "ignored",
                    },
                    ensure_ascii=False,
                )
            )


class NautobotDatabaseBackendTest(TransactionTestCase):
    """The worker's main process must be able to store a result on an idle-closed connection."""

    def setUp(self):
        super().setUp()
        self.backend = NautobotDatabaseBackend(app=celery.app)
        self.conn = connections["default"]
        self.conn.ensure_connection()

    def test_store_result_recovers_closed_connection(self):
        """A connection closed under Django, as by the server or a proxy, is reopened and the result stored."""
        self.conn.connection.close()
        task_id = uuid4()
        self.backend.store_result(task_id, None, JobResultStatusChoices.STATUS_SUCCESS)
        self.assertEqual(JobResult.objects.get(id=task_id).status, JobResultStatusChoices.STATUS_SUCCESS)

    def test_store_result_honors_conn_max_age(self):
        """A connection past CONN_MAX_AGE is replaced before the result is stored."""
        self.conn.close_at = time.monotonic() - 1
        old_connection = self.conn.connection
        self.backend.store_result(uuid4(), None, JobResultStatusChoices.STATUS_SUCCESS)
        self.assertIsNot(self.conn.connection, old_connection)
