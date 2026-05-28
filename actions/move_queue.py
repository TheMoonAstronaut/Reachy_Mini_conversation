"""Movement queue system adapted from reachy_mini_conversation_app.

This module provides:
- DanceQueueMove, EmotionQueueMove, GotoQueueMove wrappers
- MovementManager for sequential move execution
"""

from __future__ import annotations
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Any, Deque, Dict, Tuple

import numpy as np
from numpy.typing import NDArray

from reachy_mini import ReachyMini
from reachy_mini.motion.move import Move
from reachy_mini.utils import create_head_pose
from reachy_mini.utils.interpolation import (
    compose_world_offset,
    linear_pose_interpolation,
)
from reachy_mini.reachy_mini import (
    SLEEP_HEAD_POSE as SDK_SLEEP_HEAD_POSE,
    SLEEP_ANTENNAS_JOINT_POSITIONS as SDK_SLEEP_ANTENNAS,
)

logger = logging.getLogger(__name__)

CONTROL_LOOP_FREQUENCY_HZ = 60.0

FullBodyPose = Tuple[NDArray[np.float32], Tuple[float, float], float]


class BreathingMove(Move):
    """Breathing move with interpolation to neutral and then continuous breathing patterns."""

    def __init__(
        self,
        interpolation_start_pose: NDArray[np.float32],
        interpolation_start_antennas: Tuple[float, float],
        interpolation_duration: float = 1.0,
    ):
        """Initialize breathing move.

        Args:
            interpolation_start_pose: 4x4 matrix of current head pose to interpolate from
            interpolation_start_antennas: Current antenna positions to interpolate from
            interpolation_duration: Duration of interpolation to neutral (seconds)
        """
        self.interpolation_start_pose = interpolation_start_pose
        self.interpolation_start_antennas = np.array(interpolation_start_antennas)
        self.interpolation_duration = interpolation_duration

        self.neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        self.neutral_antennas = np.array([-0.1745, 0.1745])

        self.breathing_z_amplitude = 0.005
        self.breathing_frequency = 0.1
        self.antenna_sway_amplitude = np.deg2rad(15)
        self.antenna_frequency = 0.5

    @property
    def duration(self) -> float:
        return float("inf")

    def evaluate(self, t: float) -> Tuple[NDArray[np.float64] | None, NDArray[np.float64] | None, float | None]:
        if t < self.interpolation_duration:
            interpolation_t = t / self.interpolation_duration

            head_pose = linear_pose_interpolation(
                self.interpolation_start_pose,
                self.neutral_head_pose,
                interpolation_t,
            )

            antennas_interp = (
                1 - interpolation_t
            ) * self.interpolation_start_antennas + interpolation_t * self.neutral_antennas
            antennas = antennas_interp.astype(np.float64)

        else:
            breathing_time = t - self.interpolation_duration

            z_offset = self.breathing_z_amplitude * np.sin(2 * np.pi * self.breathing_frequency * breathing_time)
            head_pose = create_head_pose(x=0, y=0, z=z_offset, roll=0, pitch=0, yaw=0, degrees=True, mm=False)

            antenna_sway = self.antenna_sway_amplitude * np.sin(2 * np.pi * self.antenna_frequency * breathing_time)
            antennas = np.array([antenna_sway, -antenna_sway], dtype=np.float64)

        return (head_pose, antennas, 0.0)


class DanceQueueMove(Move):
    """Wrapper for dance moves to work with the movement queue system."""

    def __init__(self, move_name: str):
        """Initialize a DanceQueueMove."""
        from reachy_mini_dances_library.dance_move import DanceMove
        self.dance_move = DanceMove(move_name)
        self.move_name = move_name

    @property
    def duration(self) -> float:
        """Duration property required by official Move interface."""
        return float(self.dance_move.duration)

    def evaluate(self, t: float) -> Tuple[NDArray[np.float64] | None, NDArray[np.float64] | None, float | None]:
        """Evaluate dance move at time t."""
        try:
            head_pose, antennas, body_yaw = self.dance_move.evaluate(t)
            if isinstance(antennas, tuple):
                antennas = np.array([antennas[0], antennas[1]])
            return (head_pose, antennas, body_yaw)
        except Exception as e:
            logger.error(f"Error evaluating dance move '{self.move_name}' at t={t}: {e}")
            neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            return (neutral_head_pose, np.array([0.0, 0.0], dtype=np.float64), 0.0)


class EmotionQueueMove(Move):
    """Wrapper for emotion moves to work with the movement queue system."""

    def __init__(self, emotion_name: str, recorded_moves: Any):
        """Initialize an EmotionQueueMove."""
        self.emotion_move = recorded_moves.get(emotion_name)
        self.emotion_name = emotion_name

    @property
    def duration(self) -> float:
        """Duration property required by official Move interface."""
        return float(self.emotion_move.duration)

    def evaluate(self, t: float) -> Tuple[NDArray[np.float64] | None, NDArray[np.float64] | None, float | None]:
        """Evaluate emotion move at time t."""
        try:
            head_pose, antennas, body_yaw = self.emotion_move.evaluate(t)
            if isinstance(antennas, tuple):
                antennas = np.array([antennas[0], antennas[1]])
            return (head_pose, antennas, body_yaw)
        except Exception as e:
            logger.error(f"Error evaluating emotion '{self.emotion_name}' at t={t}: {e}")
            neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            return (neutral_head_pose, np.array([0.0, 0.0], dtype=np.float64), 0.0)


class GotoQueueMove(Move):
    """Wrapper for goto moves with linear interpolation."""

    def __init__(
        self,
        target_head_pose: NDArray[np.float32],
        start_head_pose: NDArray[np.float32] | None = None,
        target_antennas: Tuple[float, float] = (0, 0),
        start_antennas: Tuple[float, float] | None = None,
        target_body_yaw: float = 0,
        start_body_yaw: float | None = None,
        duration: float = 1.0,
    ):
        """Initialize a GotoQueueMove."""
        self._duration = duration
        self.target_head_pose = target_head_pose
        self.start_head_pose = start_head_pose
        self.target_antennas = target_antennas
        self.start_antennas = start_antennas or (0, 0)
        self.target_body_yaw = target_body_yaw
        self.start_body_yaw = start_body_yaw or 0

    @property
    def duration(self) -> float:
        """Duration property required by official Move interface."""
        return self._duration

    def evaluate(self, t: float) -> Tuple[NDArray[np.float64] | None, NDArray[np.float64] | None, float | None]:
        """Evaluate goto move at time t using linear interpolation."""
        try:
            t_clamped = max(0, min(1, t / self.duration))

            if self.start_head_pose is not None:
                start_pose = self.start_head_pose
            else:
                start_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)

            head_pose = linear_pose_interpolation(start_pose, self.target_head_pose, t_clamped)

            antennas = np.array(
                [
                    self.start_antennas[0] + (self.target_antennas[0] - self.start_antennas[0]) * t_clamped,
                    self.start_antennas[1] + (self.target_antennas[1] - self.start_antennas[1]) * t_clamped,
                ],
                dtype=np.float64,
            )

            body_yaw = self.start_body_yaw + (self.target_body_yaw - self.start_body_yaw) * t_clamped

            return (head_pose, antennas, body_yaw)

        except Exception as e:
            logger.error(f"Error evaluating goto move at t={t}: {e}")
            target_head_pose_f64 = self.target_head_pose.astype(np.float64)
            target_antennas_array = np.array([self.target_antennas[0], self.target_antennas[1]], dtype=np.float64)
            return (target_head_pose_f64, target_antennas_array, self.target_body_yaw)


def combine_full_body(primary_pose: FullBodyPose, secondary_pose: FullBodyPose) -> FullBodyPose:
    """Combine primary and secondary full body poses."""
    primary_head, primary_antennas, primary_body_yaw = primary_pose
    secondary_head, secondary_antennas, secondary_body_yaw = secondary_pose

    combined_head = compose_world_offset(primary_head, secondary_head, reorthonormalize=False)
    combined_antennas = (
        primary_antennas[0] + secondary_antennas[0],
        primary_antennas[1] + secondary_antennas[1],
    )
    combined_body_yaw = primary_body_yaw + secondary_body_yaw

    return (combined_head, combined_antennas, combined_body_yaw)


def clone_full_body_pose(pose: FullBodyPose) -> FullBodyPose:
    """Create a deep copy of a full body pose tuple."""
    head, antennas, body_yaw = pose
    return (head.copy(), (float(antennas[0]), float(antennas[1])), float(body_yaw))


@dataclass
class MovementState:
    """State tracking for the movement system."""

    current_move: Move | None = None
    move_start_time: float | None = None
    last_activity_time: float = 0.0
    speech_offsets: Tuple[float, float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    face_tracking_offsets: Tuple[float, float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    last_primary_pose: FullBodyPose | None = None

    def update_activity(self) -> None:
        """Update the last activity time."""
        self.last_activity_time = time.monotonic()


class MovementManager:
    """Coordinate sequential moves and robot output at 60 Hz."""

    def __init__(self, current_robot: ReachyMini, camera_worker: Any = None):
        """Initialize movement manager."""
        self.current_robot = current_robot
        self.camera_worker = camera_worker

        self._now = time.monotonic
        self.state = MovementState()
        self.state.last_activity_time = self._now()
        neutral_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        self.state.last_primary_pose = (neutral_pose, (0.0, 0.0), 0.0)

        self.move_queue: Deque[Move] = deque()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.idle_inactivity_delay = 0.3
        self._breathing_active = False
        self._sleep_mode = False

        self._command_queue: Queue = Queue()
        self._speech_offsets_lock = threading.Lock()
        self._pending_speech_offsets = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self._speech_offsets_dirty = False

        self._face_offsets_lock = threading.Lock()
        self._pending_face_offsets = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self._face_offsets_dirty = False

        self._status_lock = threading.Lock()
        self._last_commanded_pose: FullBodyPose = clone_full_body_pose(self.state.last_primary_pose)

    def queue_move(self, move: Move) -> None:
        """Queue a primary move to run after the current one."""
        self._command_queue.put(("queue_move", move))

    def clear_move_queue(self) -> None:
        """Stop the active move and discard any queued moves."""
        self._command_queue.put(("clear_queue", None))

    def set_moving_state(self, duration: float) -> None:
        """Mark the robot as actively moving."""
        self._command_queue.put(("set_moving_state", duration))

    def set_speech_offsets(self, offsets: Tuple[float, float, float, float, float, float]) -> None:
        """Update speech-induced secondary offsets."""
        with self._speech_offsets_lock:
            self._pending_speech_offsets = offsets
            self._speech_offsets_dirty = True

    def queue_move_and_wait(self, move: Move, timeout: float = 30.0) -> bool:
        """Queue a move and wait for it to complete."""
        event = threading.Event()
        self._command_queue.put(("queue_move_with_callback", (move, event)))
        return event.wait(timeout=timeout)

    def _poll_signals(self, current_time: float) -> None:
        """Apply queued commands."""
        self._apply_pending_offsets()

        while True:
            try:
                command, payload = self._command_queue.get_nowait()
            except Empty:
                break
            self._handle_command(command, payload, current_time)

    def _apply_pending_offsets(self) -> None:
        """Apply pending offset updates."""
        with self._speech_offsets_lock:
            if self._speech_offsets_dirty:
                self.state.speech_offsets = self._pending_speech_offsets
                self.state.update_activity()
                self._speech_offsets_dirty = False

        with self._face_offsets_lock:
            if self._face_offsets_dirty:
                self.state.face_tracking_offsets = self._pending_face_offsets
                self.state.update_activity()
                self._face_offsets_dirty = False

    def set_sleep_mode(self, enabled: bool) -> None:
        """Enter or exit sleep mode. In sleep mode, breathing is suppressed."""
        self._command_queue.put(("set_sleep_mode", enabled))

    def _handle_command(self, command: str, payload: Any, current_time: float) -> None:
        """Handle a cross-thread command."""
        if command == "queue_move":
            if isinstance(payload, Move):
                self.move_queue.append(payload)
                self.state.update_activity()
                logger.debug(f"Queued move: {type(payload).__name__}, queue size: {len(self.move_queue)}")
            else:
                logger.warning(f"Ignored queue_move with invalid payload: {payload}")
        elif command == "queue_move_with_callback":
            move, event = payload
            if isinstance(move, Move):
                self.move_queue.append(move)
                self.state.update_activity()
                self._pending_callback_event = event
                logger.debug(f"Queued move with callback: {move}")
        elif command == "clear_queue":
            self.move_queue.clear()
            self.state.current_move = None
            self.state.move_start_time = None
            logger.info("Cleared move queue and stopped current move")
        elif command == "set_moving_state":
            self.state.update_activity()
        elif command == "mark_activity":
            self.state.update_activity()
        elif command == "set_sleep_mode":
            self._sleep_mode = bool(payload)
            logger.info(f"Sleep mode {'enabled' if self._sleep_mode else 'disabled'}")

    def _manage_move_queue(self, current_time: float) -> None:
        """Manage the primary move queue."""
        if self.state.current_move is None or (
            self.state.move_start_time is not None
            and current_time - self.state.move_start_time >= self.state.current_move.duration
        ):
            self.state.current_move = None
            self.state.move_start_time = None

            if self.move_queue:
                self.state.current_move = self.move_queue.popleft()
                self.state.move_start_time = current_time
                self._breathing_active = isinstance(self.state.current_move, BreathingMove)
                logger.debug(f"Starting new move: {type(self.state.current_move).__name__}")

                if hasattr(self, "_pending_callback_event") and self._pending_callback_event:
                    self._move_started_time = current_time

    def _manage_breathing(self, current_time: float) -> None:
        """Manage automatic breathing when idle."""
        if self._sleep_mode:
            return

        if (
            self.state.current_move is None
            and not self.move_queue
            and not self._breathing_active
        ):
            idle_for = current_time - self.state.last_activity_time
            if idle_for >= self.idle_inactivity_delay:
                try:
                    _, current_antennas = self.current_robot.get_current_joint_positions()
                    current_head_pose = self.current_robot.get_current_head_pose()

                    self._breathing_active = True
                    self.state.update_activity()

                    breathing_move = BreathingMove(
                        interpolation_start_pose=current_head_pose,
                        interpolation_start_antennas=current_antennas,
                        interpolation_duration=1.0,
                    )
                    self.move_queue.append(breathing_move)
                    logger.debug(f"Started breathing after {idle_for:.1f}s of inactivity")
                except Exception as e:
                    self._breathing_active = False
                    logger.error(f"Failed to start breathing: {e}")

        if isinstance(self.state.current_move, BreathingMove) and self.move_queue:
            self.state.current_move = None
            self.state.move_start_time = None
            self._breathing_active = False
            logger.debug("Stopping breathing due to new move activity")

        if self.state.current_move is not None and not isinstance(self.state.current_move, BreathingMove):
            self._breathing_active = False

    def _get_primary_pose(self, current_time: float) -> FullBodyPose:
        """Get the primary full body pose from current move or neutral."""
        if self.state.current_move is not None and self.state.move_start_time is not None:
            move_time = current_time - self.state.move_start_time
            head, antennas, body_yaw = self.state.current_move.evaluate(move_time)

            if head is None:
                head = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            if antennas is None:
                antennas = np.array([-0.1745, 0.1745])
            if body_yaw is None:
                body_yaw = 0.0

            antennas_tuple = (float(antennas[0]), float(antennas[1]))
            head_copy = head.copy()
            primary_full_body_pose = (head_copy, antennas_tuple, float(body_yaw))

            self.state.last_primary_pose = clone_full_body_pose(primary_full_body_pose)

            if hasattr(self, "_pending_callback_event") and self._pending_callback_event:
                event = self._pending_callback_event
                del self._pending_callback_event
                event.set()

        elif self.state.last_primary_pose is not None:
            primary_full_body_pose = clone_full_body_pose(self.state.last_primary_pose)
        else:
            neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            primary_full_body_pose = (neutral_head_pose, (0.0, 0.0), 0.0)
            self.state.last_primary_pose = clone_full_body_pose(primary_full_body_pose)

        return primary_full_body_pose

    def _get_secondary_pose(self) -> FullBodyPose:
        """Get secondary pose from offsets."""
        current_offsets = (
            self.state.speech_offsets[0] + self.state.face_tracking_offsets[0],
            self.state.speech_offsets[1] + self.state.face_tracking_offsets[1],
            self.state.speech_offsets[2] + self.state.face_tracking_offsets[2],
            self.state.speech_offsets[3] + self.state.face_tracking_offsets[3],
            self.state.speech_offsets[4] + self.state.face_tracking_offsets[4],
            self.state.speech_offsets[5] + self.state.face_tracking_offsets[5],
        )

        secondary_head_pose = create_head_pose(
            x=current_offsets[0],
            y=current_offsets[1],
            z=current_offsets[2],
            roll=current_offsets[3],
            pitch=current_offsets[4],
            yaw=current_offsets[5],
            degrees=False,
            mm=False,
        )
        return (secondary_head_pose, (0.0, 0.0), 0.0)

    def _compose_full_body_pose(self, current_time: float) -> FullBodyPose:
        """Compose primary and secondary poses."""
        primary = self._get_primary_pose(current_time)
        secondary = self._get_secondary_pose()
        return combine_full_body(primary, secondary)

    def _issue_control_command(
        self,
        head: NDArray[np.float32],
        antennas: Tuple[float, float],
        body_yaw: float,
    ) -> None:
        """Send pose to robot."""
        try:
            self.current_robot.set_target(head=head, antennas=antennas, body_yaw=body_yaw)
        except Exception as e:
            logger.error(f"Failed to set robot target: {e}")
        else:
            with self._status_lock:
                self._last_commanded_pose = clone_full_body_pose((head, antennas, body_yaw))

    def start(self) -> None:
        """Start the worker thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Move worker already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.working_loop, daemon=True)
        self._thread.start()
        logger.info("Move worker started")

    def stop(self) -> None:
        """Stop the worker thread and reset to neutral."""
        if self._thread is None or not self._thread.is_alive():
            return

        logger.info("Stopping movement manager...")

        self.clear_move_queue()
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        try:
            neutral_head_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
            neutral_antennas = [-0.1745, 0.1745]
            self.current_robot.goto_target(
                head=neutral_head_pose,
                antennas=neutral_antennas,
                duration=2.0,
                body_yaw=0.0,
            )
            logger.info("Reset to neutral position completed")
        except Exception as e:
            logger.error(f"Failed to reset to neutral position: {e}")

    def is_idle(self) -> bool:
        """Return True when robot is idle (no moves running and inactive long enough)."""
        return (
            self.state.current_move is None
            and not self.move_queue
            and self._now() - self.state.last_activity_time > self.idle_inactivity_delay
        )

    def is_breathing_active(self) -> bool:
        """Return True when breathing move is running."""
        return self._breathing_active

    def is_sleep_mode(self) -> bool:
        """Return True when in sleep mode (no autonomous breathing)."""
        return self._sleep_mode

    def is_at_sleep_pose(self, threshold: float = 0.1) -> bool:
        """Check if current pose is close to sleep pose."""
        if self.state.last_primary_pose is None:
            return False

        head, antennas, _ = self.state.last_primary_pose

        sleep_head = SDK_SLEEP_HEAD_POSE.astype(np.float32)
        sleep_antennas = SDK_SLEEP_ANTENNAS

        head_diff = np.abs(head - sleep_head).max()
        ant_diff = max(abs(antennas[0] - sleep_antennas[0]), abs(antennas[1] - sleep_antennas[1]))

        return head_diff < threshold and ant_diff < threshold

    def is_goto_or_sleep_in_progress(self) -> bool:
        """Return True if a GotoQueueMove or breathing is currently running."""
        if isinstance(self.state.current_move, BreathingMove):
            return True
        if isinstance(self.state.current_move, GotoQueueMove):
            return True
        return False

    def working_loop(self) -> None:
        """Main control loop running at 60 Hz."""
        logger.info("Starting movement control loop at 60 Hz")

        target_period = 1.0 / CONTROL_LOOP_FREQUENCY_HZ

        while not self._stop_event.is_set():
            loop_start = self._now()

            self._poll_signals(loop_start)
            self._manage_move_queue(loop_start)
            self._manage_breathing(loop_start)

            head, antennas, body_yaw = self._compose_full_body_pose(loop_start)

            self._issue_control_command(head, antennas, body_yaw)

            elapsed = self._now() - loop_start
            sleep_time = max(0, target_period - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        logger.info("Movement control loop stopped")
