"""Django management command to fetch API content and process for RAG."""

from django.core.management.base import BaseCommand, CommandError

from knowledge.services.api_content_processor import (
    APIContentRAGProcessor,
    APIContentProcessingError,
)


class Command(BaseCommand):
    help = "Fetch content from API and process for RAG"

    def add_arguments(self, parser):
        parser.add_argument(
            "api_url",
            type=str,
            help="API endpoint URL to fetch content from",
        )
        parser.add_argument(
            "--document-name",
            type=str,
            default="API Content",
            help="Name for the virtual document (default: 'API Content')",
        )
        parser.add_argument(
            "--items-key",
            type=str,
            default="results",
            help="JSON key containing the items list (default: 'results')",
        )

    def handle(self, *args, **options):
        import requests

        api_url = options["api_url"]
        document_name = options["document_name"]
        items_key = options["items_key"]

        try:
            # Fetch from API
            self.stdout.write(self.style.WARNING(f"Fetching from {api_url}..."))
            response = requests.get(api_url, timeout=30)
            response.raise_for_status()

            data = response.json()
            items = data.get(items_key, [])

            if not items:
                raise CommandError(f"No items found under key '{items_key}'")

            self.stdout.write(
                self.style.SUCCESS(f"Fetched {len(items)} items from API")
            )

            # Process for RAG
            self.stdout.write(self.style.WARNING("Processing content for RAG..."))
            processor = APIContentRAGProcessor(document_name=document_name)
            stats = processor.process_items(items)

            self.stdout.write(
                self.style.SUCCESS(
                    f"\nRAG processing completed:\n"
                    f"  Items processed: {stats['processed']}\n"
                    f"  Chunks created: {stats['chunks_created']}\n"
                    f"  Errors: {stats['errors']}"
                )
            )

            self.stdout.write(
                self.style.SUCCESS("\nContent is now available in RAG queries!")
            )

        except (APIContentProcessingError, CommandError) as e:
            raise CommandError(f"Processing failed: {e}")
        except requests.RequestException as e:
            raise CommandError(f"API request failed: {e}")
        except Exception as e:
            raise CommandError(f"Unexpected error: {e}")
