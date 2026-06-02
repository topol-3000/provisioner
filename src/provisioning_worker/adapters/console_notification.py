"""Console (stdout) notification transport — dev-only.

``ConsoleNotificationTransport`` implements the ``NotificationTransport``
Protocol by writing credential information directly to ``sys.stdout``.
This is the ONLY place in the codebase where ``admin_password`` is
written to any output channel (D-12).

IMPORTANT: This transport must NEVER use structlog. Credentials must not
enter the log pipeline under any circumstances (ASVS V7, D-12, T-3-04).
The ``# dev-only`` annotation marks this adapter as unsuitable for
production; milestone 2 replaces it with ``SmtpNotificationTransport``.
"""

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from provisioning_worker.ports.notification_transport import CredentialNotification

__all__ = ["ConsoleNotificationTransport"]


class ConsoleNotificationTransport:  # dev-only
    """Credential transport that writes directly to ``sys.stdout``.

    Used in development and testing only. Production deployments must
    use ``SmtpNotificationTransport`` (milestone 2).

    The ``send_credentials`` method deliberately uses ``sys.stdout.write``
    (NOT ``print`` with default end, NOT structlog) so that:
    - Output is explicit and testable via monkeypatch on ``sys.stdout``.
    - Credentials never enter the structlog pipeline (D-12, T-3-04).
    - The ``# dev-only`` class is obviously marked as non-production.
    """

    async def send_credentials(self, notification: CredentialNotification) -> None:
        """Write instance credentials to stdout.

        This is the only sanctioned output channel for credentials in M1 (D-12).
        The admin_password appears here and nowhere else.

        Args:
            notification: The credential payload for the newly-provisioned instance.
        """
        # dev-only: write directly to sys.stdout — NOT via structlog (D-12)
        sys.stdout.write(
            f"[CREDENTIALS] Instance {notification.instance_id} is ready\n"
            f"  Recipient: {notification.recipient_email}\n"
            f"  URL: {notification.instance_url}\n"
            f"  Login: {notification.admin_login}\n"
            f"  Password: {notification.admin_password}\n"
        )
        sys.stdout.flush()
