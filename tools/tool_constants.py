from enum import Enum


class ToolState(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SystemTool(Enum):
    TASK_STATUS = "task_status"
    TASK_CANCEL = "task_cancel"