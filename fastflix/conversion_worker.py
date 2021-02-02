# -*- coding: utf-8 -*-
import logging
from pathlib import Path
from queue import Empty
from typing import Optional

import reusables
from appdirs import user_data_dir
from box import Box

from fastflix.command_runner import BackgroundRunner
from fastflix.language import t
from fastflix.shared import file_date
from fastflix.models.queue import get_queue, save_queue
from fastflix.models.video import Video

logger = logging.getLogger("fastflix-core")

log_path = Path(user_data_dir("FastFlix", appauthor=False, roaming=True)) / "logs"
after_done_path = Path(user_data_dir("FastFlix", appauthor=False, roaming=True)) / "after_done_logs"

queue_path = Path(user_data_dir("FastFlix", appauthor=False, roaming=True)) / "queue.yaml"
queue_lock_file = Path(user_data_dir("FastFlix", appauthor=False, roaming=True)) / "queue.lock"


CONTINUOUS = 0x80000000
SYSTEM_REQUIRED = 0x00000001


def prevent_sleep_mode():
    """https://msdn.microsoft.com/en-us/library/windows/desktop/aa373208(v=vs.85).aspx"""
    if reusables.win_based:
        import ctypes

        try:
            ctypes.windll.kernel32.SetThreadExecutionState(CONTINUOUS | SYSTEM_REQUIRED)
        except Exception:
            logger.exception("Could not prevent system from possibly going to sleep during conversion")
        else:
            logger.debug("System has been asked to not sleep")


def allow_sleep_mode():
    if reusables.win_based:
        import ctypes

        try:
            ctypes.windll.kernel32.SetThreadExecutionState(CONTINUOUS)
        except Exception:
            logger.exception("Could not allow system to resume sleep mode")
        else:
            logger.debug("System has been allowed to enter sleep mode again")


def get_next_video() -> Optional[Video]:
    queue = get_queue()
    for video in queue:
        if (
            not video.status.complete
            and not video.status.success
            and not video.status.cancelled
            and not video.status.error
            and not video.status.running
        ):
            return video


def set_status(
    current_video,
    completed=None,
    success=None,
    cancelled=None,
    errored=None,
    running=None,
    next_command=False,
    reset_commands=False,
):
    queue = get_queue()
    for video in queue:
        if video.uuid == current_video.uuid:
            if completed is not None:
                video.status.complete = completed
            if cancelled is not None:
                video.status.cancelled = cancelled
            if errored is not None:
                video.status.error = errored
            if success is not None:
                video.status.success = success
            if running is not None:
                video.status.running = running

            if completed or cancelled or errored or success:
                video.status.running = False

            if next_command:
                video.status.current_command += 1
            if reset_commands:
                video.status.current_command = 0
            break
    else:
        logger.error(f"Could not find video in queue to update status of!\n {current_video}")
        return
    save_queue(queue)


@reusables.log_exception(log="fastflix-core")
def queue_worker(gui_proc, worker_queue, status_queue, log_queue):
    runner = BackgroundRunner(log_queue=log_queue)

    # Command looks like (video_uuid, command_uuid, command, work_dir)
    after_done_command = ""
    gui_died = False
    currently_encoding = False
    paused = False
    video: Optional[Video] = None

    def current_command():
        nonlocal video

    def start_command():
        nonlocal currently_encoding
        log_queue.put(
            f"CLEAR_WINDOW:{video.uuid}:{video.video_settings.conversion_commands[video.status.current_command]['uuid']}"
        )
        reusables.remove_file_handlers(logger)
        new_file_handler = reusables.get_file_handler(
            log_path
            / f"flix_conversion_{video.video_settings.video_title or video.video_settings.output_path.stem}_{file_date()}.log",
            level=logging.DEBUG,
            log_format="%(asctime)s - %(message)s",
            encoding="utf-8",
        )
        logger.addHandler(new_file_handler)
        prevent_sleep_mode()
        currently_encoding = True
        runner.start_exec(
            video.video_settings.conversion_commands[video.status.current_command]["command"],
            work_dir=str(video.work_path),
        )
        set_status(video, running=True)
        status_queue.put(("queue",))

        # status_queue.put(("running", commands_to_run[0][0], commands_to_run[0][1], runner.started_at.isoformat()))

    while True:
        if currently_encoding and not runner.is_alive():
            reusables.remove_file_handlers(logger)
            if runner.error_detected:
                logger.info(t("Error detected while converting"))

                # Stop working!
                currently_encoding = False
                set_status(video, errored=True)
                status_queue.put(("error",))
                commands_to_run = []
                allow_sleep_mode()
                if gui_died:
                    return
                continue

            # Successfully encoded, do next one if it exists
            # First check if the current video has more commands
            video.status.current_command += 1

            if len(video.video_settings.conversion_commands) > video.status.current_command:
                logger.debug("About to run next command for this video")
                set_status(video, next_command=True)
                status_queue.put(("queue",))
                start_command()
                continue
            else:
                set_status(video, next_command=True, completed=True)
                status_queue.put(("queue",))
                video = None

            if paused:
                currently_encoding = False
                allow_sleep_mode()
                logger.debug(t("Queue has been paused"))
                continue

            if video := get_next_video():
                start_command()
                continue
            else:
                currently_encoding = False
                allow_sleep_mode()
                logger.info(t("all conversions complete"))
                if after_done_command:
                    logger.info(f"{t('Running after done command:')} {after_done_command}")
                    try:
                        runner.start_exec(after_done_command, str(after_done_path))
                    except Exception:
                        logger.exception("Error occurred while running after done command")
                        continue
            if gui_died:
                return

        if not gui_died and not gui_proc.is_alive():
            gui_proc.join()
            gui_died = True
            if runner.is_alive() or currently_encoding:
                logger.info(t("The GUI might have died, but I'm going to keep converting!"))
            else:
                logger.debug(t("Conversion worker shutting down"))
                return

        try:
            request = worker_queue.get(block=True, timeout=0.05)
        except Empty:
            continue
        except KeyboardInterrupt:
            status_queue.put(("exit",))
            allow_sleep_mode()
            return
        else:
            if request[0] == "add_items":

                # Request looks like (queue command, log_dir, (commands))
                log_path = Path(request[1])
                if not currently_encoding and not paused:
                    video = get_next_video()
                    if video:
                        start_command()

                # for command in request[2]:
                #     if command not in commands_to_run:
                #         logger.debug(t(f"Adding command to the queue for {command[4]} - {command[2]}"))
                #         commands_to_run.append(command)
                #     # else:
                #     #     logger.debug(t(f"Command already in queue: {command[1]}"))
                # if not runner.is_alive() and not paused:
                #     logger.debug(t("No encoding is currently in process, starting encode"))
                #     start_command()
            if request[0] == "cancel":
                logger.debug(t("Cancel has been requested, killing encoding"))
                runner.kill()
                set_status(video, reset_commands=True, cancelled=True)
                currently_encoding = False
                allow_sleep_mode()
                status_queue.put(("cancelled", video.uuid))

            if request[0] == "pause queue":
                logger.debug(t("Command worker received request to pause encoding after the current item completes"))
                paused = True

            if request[0] == "resume queue":
                paused = False
                logger.debug(t("Command worker received request to resume encoding"))
                if not currently_encoding:
                    if not video:
                        video = get_next_video()
                    start_command()

            if request[0] == "set after done":
                after_done_command = request[1]
                if after_done_command:
                    logger.debug(f'{t("Setting after done command to:")} {after_done_command}')
                else:
                    logger.debug(t("Removing after done command"))

            if request[0] == "pause encode":
                logger.debug(t("Command worker received request to pause current encode"))
                try:
                    runner.pause()
                except Exception:
                    logger.exception("Could not pause command")
                else:
                    status_queue.put(("paused encode",))
            if request[0] == "resume encode":
                logger.debug(t("Command worker received request to resume paused encode"))
                try:
                    runner.resume()
                except Exception:
                    logger.exception("Could not resume command")
                else:
                    status_queue.put(("resumed encode",))
