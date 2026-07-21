import hashlib
from datetime import timedelta

from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from reporter.models import Dashboard, GrafanaConnection, Invitation, User


TEST_KEY = Fernet.generate_key().decode("ascii")


@override_settings(APP_ENCRYPTION_KEY=TEST_KEY)
class ModelSecurityTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin@example.com", "A-strong-password-123")
        self.connection = GrafanaConnection.objects.create(base_url="https://grafana.example.com")

    def test_grafana_credentials_are_encrypted(self):
        self.connection.set_username("service-user")
        self.connection.set_password("secret-value")
        self.connection.save()
        self.connection.refresh_from_db()
        self.assertNotIn("service-user", self.connection.username_ciphertext)
        self.assertNotIn("secret-value", self.connection.password_ciphertext)
        self.assertEqual(self.connection.get_username(), "service-user")
        self.assertEqual(self.connection.get_password(), "secret-value")

    def test_dashboard_rejects_other_hosts(self):
        dashboard = Dashboard(name="bad", url="https://other.example.com/d/uid/name")
        with self.assertRaises(ValidationError):
            dashboard.save()
        valid = Dashboard.objects.create(name="good", url="/d/uid/name")
        self.assertEqual(valid.name, "good")

    def test_invitation_token_is_hashed_and_expires(self):
        invitation, token = Invitation.issue(email="new@example.com", role=User.Role.VIEWER, invited_by=self.admin)
        self.assertNotEqual(invitation.token_hash, token)
        self.assertEqual(invitation.token_hash, hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(Invitation.find_valid(token), invitation)
        invitation.expires_at = timezone.now() - timedelta(seconds=1)
        invitation.save(update_fields=["expires_at"])
        with self.assertRaises(ValidationError):
            Invitation.find_valid(token)

    def test_superusers_are_always_admins(self):
        self.admin.role = User.Role.VIEWER
        self.admin.save()
        self.assertEqual(self.admin.role, User.Role.ADMIN)
