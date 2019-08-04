from collections import Counter
from ..functions import GithubWebhookProcessor


def process_webhooks(event, context) -> Counter:
    processor = GithubWebhookProcessor()
    processed_events = processor.process_webhook_events()
    return processed_events
