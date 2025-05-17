import logging
from abc import abstractmethod

from ...models import SlackCommand

logger = logging.getLogger(__name__)


class SubCommandBase:
    """Base class for all command classes."""

    ALIASES = {}

    @abstractmethod
    def handle(self, command: SlackCommand):
        """Handle the specific command."""
        raise NotImplementedError("Subclasses must implement this method.")
