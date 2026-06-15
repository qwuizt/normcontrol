"""
Progress tracking utility for long-running operations.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ProgressTracker:
    """
    A progress tracker that sends real-time updates via RabbitMQ.

    This class handles progress tracking for iterative operations,
    automatically calculating percentages and sending updates at configurable intervals
    to prevent message spam while providing meaningful user feedback.

    Example:
        tracker = create_progress_tracker(
            run_id="12345",
            task_id="pdf_processing",
            channel_id=123456789,
            message_id=42,
            total_items=100,
            task_display_name="PDF Processing"
        )

        for i in range(100):
            # Process item...
            if tracker:
                tracker.increment()
    """

    def __init__(
        self,
        run_id: str,
        task_id: str,
        channel_id: int,
        message_id: int,
        total_items: int,
        task_display_name: Optional[str] = None,
        min_percent_diff: int = 10,
    ):
        """
        Initialize the progress tracker.

        Args:
            run_id: Unique identifier for the current run
            task_id: Identifier for the specific task
            channel_id: Telegram channel ID for notifications
            message_id: Telegram message ID to update
            total_items: Total number of items to process
            task_display_name: Human-readable task name for display
            min_percent_diff: Minimum percentage difference before sending update
        """
        self.run_id = run_id
        self.task_id = task_id
        self.channel_id = channel_id
        self.message_id = message_id
        self.total_items = total_items
        self.task_display_name = task_display_name
        self.min_percent_diff = min_percent_diff

        self.current_item = 0
        self.last_reported_percent = -1

    def _calculate_percent(self) -> int:
        """Calculate current progress percentage."""
        if self.total_items == 0:
            return 100
        return min(100, int((self.current_item / self.total_items) * 100))

    def _should_report_progress(self) -> bool:
        """Check if progress should be reported based on threshold."""
        current_percent = self._calculate_percent()
        return (
            current_percent - self.last_reported_percent >= self.min_percent_diff
            or current_percent == 100
        )

    def _send_progress_update(self, progress_percent: int) -> None:
        """Send progress update using synchronous RabbitMQ client."""
        try:
            from src.communication.message_producer import oneshot_queue_publish

            message = {
                'run_id': self.run_id,
                'status': 'running',
                'task_id': self.task_id,
                'task_display_name': self.task_display_name,
                'channel_id': self.channel_id,
                'message_id': self.message_id,
                'progress_percent': progress_percent,
            }

            oneshot_queue_publish(message)
            logger.debug(f'Progress sent: {progress_percent}% for task {self.task_id}')

        except Exception as e:
            logger.warning(f'Failed to send progress update: {e}')

    def update(self, current_item: Optional[int] = None) -> None:
        """
        Update progress and send notification if threshold is met.

        Args:
            current_item: Current item number (0-based). If None, increment by 1.
        """
        if current_item is not None:
            self.current_item = current_item
        else:
            self.current_item += 1

        if self._should_report_progress():
            current_percent = self._calculate_percent()
            self._send_progress_update(current_percent)
            self.last_reported_percent = current_percent

    def increment(self) -> None:
        """Increment current item by 1 and update progress."""
        self.update()

    def set_completed(self) -> None:
        """Mark the operation as completed (100%)."""
        self.current_item = self.total_items
        current_percent = self._calculate_percent()
        self._send_progress_update(current_percent)
        self.last_reported_percent = current_percent

    def get_progress_info(self) -> dict:
        """Get current progress information."""
        return {
            'current_item': self.current_item,
            'total_items': self.total_items,
            'progress_percent': self._calculate_percent(),
            'task_id': self.task_id,
            'task_display_name': self.task_display_name,
        }


def create_progress_tracker(
    run_id: Optional[str],
    task_id: Optional[str],
    channel_id: Optional[int],
    message_id: Optional[int],
    total_items: int,
    task_display_name: Optional[str] = None,
    min_percent_diff: int = 10,
) -> Optional[ProgressTracker]:
    """
    Create a progress tracker if all required parameters are provided.

    This function provides graceful degradation - if any required
    parameter is missing, it returns None, allowing the calling code to
    handle cases where progress tracking is not available.

    Args:
        run_id: Unique identifier for the current run
        task_id: Identifier for the specific task
        channel_id: Telegram channel ID for notifications
        message_id: Telegram message ID to update
        total_items: Total number of items to process
        task_display_name: Human-readable task name for display
        min_percent_diff: Minimum percentage difference before sending update

    Returns:
        ProgressTracker instance or None if required parameters are missing
    """
    if not all([run_id, task_id, channel_id, message_id]):
        logger.debug("Progress tracking disabled - missing required parameters")
        return None

    return ProgressTracker(
        run_id=run_id,
        task_id=task_id,
        channel_id=channel_id,
        message_id=message_id,
        total_items=total_items,
        task_display_name=task_display_name,
        min_percent_diff=min_percent_diff,
    )
