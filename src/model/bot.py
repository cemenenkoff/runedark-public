import ctypes
import platform
import random
import threading
import time
import warnings
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from controller.bot_controller import BotController

import customtkinter
from customtkinter.windows.ctk_toplevel import CTkToplevel

import utilities.debug as debug
import utilities.random_util as rd
from model.window import Window
from utilities.geometry import Point
from utilities.mouse import Mouse
from utilities.options_builder import OptionsBuilder

warnings.filterwarnings("ignore", category=UserWarning)


class BotThread(threading.Thread):
    def __init__(self, target: callable) -> None:
        """Initialize a new `BotThread`.

        Args:
            target (callable): The target function to run in the thread.
        """
        threading.Thread.__init__(self, daemon=True)
        self.target = target

    def run(self) -> None:
        """Execute the target function in the thread.

        This method is called when the thread is started. It will print a message when
        the thread starts and stops.
        """
        try:
            print("Thread started.")
            self.target()
        finally:
            print("Thread stopped successfully.")

    def __get_id(self) -> Optional[int]:
        """Retrieve the unique identifier of the thread.

        Returns:
            Optional[int]: The ID of the thread, or None if the thread ID cannot be
                found.
        """
        if hasattr(self, "_thread_id"):
            return self._thread_id
        for id, thread in threading._active.items():
            if thread is self:
                return id

    def stop(self) -> None:
        """Terminate the thread by raising a `SystemExit` exception.

        This method can be called from the main thread. It is advisable to follow this
        call with a `join()` to ensure the thread exits cleanly.

        Raises:
            Exception: If there is an issue raising the exception in the thread.
        """
        thread_id = self.__get_id()
        if platform.system() == "Windows":
            res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                thread_id, ctypes.py_object(SystemExit)
            )
            if res > 1:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, 0)
                print(f"Failed to raise exception in thread {self.__get_id()}")
        elif platform.system() == "Linux":
            res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_long(thread_id), ctypes.py_object(SystemExit)
            )
            if res > 1:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(thread_id), 0)
                print(f"Failed to raise exception in thread {self.__get_id()}")


class BotStatus(Enum):
    """Enumeration describing general states of the `Bot`."""

    RUNNING = 1
    PAUSED = 2
    STOPPED = 3
    CONFIGURING = 4
    CONFIGURED = 5


class Bot(ABC):
    """A  base class for bot script models.

    `Bot` is abstract and cannot be instantiated. Many of the methods in this base
    class are pre-implemented and can be used by subclasses, or called by the
    controller, so modifying this class definition should be done carefully.
    """

    mouse = Mouse()
    options_set: bool = True
    progress: float = 0
    status = BotStatus.STOPPED
    thread: BotThread = None

    @abstractmethod
    def __init__(
        self,
        game_title: str,
        bot_title: str,
        description: str,
        window: Window,
    ) -> None:
        """Initialize a `Bot` object.

        Note that this constructor must be called by subclasses of `Bot` and not `Bot`
        itself because `Bot` is subclassed from `ABC` (i.e. Abstract Base Class). By
        making this constructor an abstract method, it ensures any subclasses that
        don't override `__init__` will also be considered abstract and therefore unable
        to be instantiated.

        Args:
            game_title (str): Title of the game the bot will interact with.
            bot_title (str): Title of the bot to display in the UI.
            description (str): Description of the bot to display in the UI.
            window (Window): Window object the bot will use to interact with the game client.
        """
        self.game_title = game_title
        self.bot_title = bot_title
        self.description = description
        self.options_builder = OptionsBuilder(bot_title)
        self.win = window

    @abstractmethod
    def main_loop(self) -> None:
        """Main logic of the bot. This function is called in a separate thread."""
        pass

    @abstractmethod
    def create_options(self) -> None:
        """Define the bot's configuration by filling out an `OptionsBuilder`."""
        pass

    @abstractmethod
    def save_options(self, options: dict):
        """Save a dictionary of options as properties of the bot.

        Args:
            options (dict): Dictionary of options to save.
        """
        pass

    def get_options_view(self, parent: CTkToplevel) -> customtkinter.CTkFrame:
        """Build the bot's options view based on those defined in `OptionsBuilder`."""
        self.clear_log()
        self.log_msg("Options panel opened.")
        self.create_options()
        view = self.options_builder.build_ui(parent, self.controller)
        view.grid(padx=0)
        self.options_builder.options = {}
        return view

    def play(self) -> None:
        """Launch the bot.

        This function performs necessary set up on the UI and then locates and
        initializes the game client window. Lastly, it launches the bot's main loop in
        a separate thread.
        """
        if self.status in [BotStatus.STOPPED, BotStatus.CONFIGURED]:
            self.clear_log()
            self.log_msg("Starting bot...")
            if not self.options_set:
                self.log_msg("Options not set. Please set options before starting.")
                return
            try:
                if not self.__initialize_window():
                    self.stop()
                    return
            except Exception as exc:
                self.log_msg(str(exc))
                self.stop()
                return
            self.reset_progress()
            self.set_status(BotStatus.RUNNING)
            self.thread = BotThread(target=self.main_loop)
            self.thread.start()
        elif self.status == BotStatus.RUNNING:
            self.log_msg("Bot is running.")
        elif self.status == BotStatus.CONFIGURING:
            self.log_msg("Please finish configuring the bot before starting.")

    def __initialize_window(self):
        """Focus and initialize the game window by identifying core UI elements."""
        self.win.focus()
        time.sleep(0.5)
        try:
            initialized_successfully = self.win.initialize()
            if not initialized_successfully:
                msg = (
                    "Game window found, but the bot couldn't orient itself. Ensure"
                    " the game displays the correct reference images to help the bot"
                    " get started before trying again."
                )
                self.log_msg(msg)
            return initialized_successfully
        except Exception as exc:
            self.log_msg(f"Error during window initialization: {exc}")
            return False

    def stop(self) -> None:
        """Stop the bot."""
        self.log_msg("Stopping script...")
        if self.status != BotStatus.STOPPED and self.thread is not None:
            self.set_status(BotStatus.STOPPED)
            self.thread.stop()
            self.thread.join()
        else:
            self.log_msg("Bot is stopped.")

    # --- Controller ---
    def set_controller(self, controller: "BotController"):
        """Set the the associated controller for this `Bot`.

        Args:
            controller (BotController): The associated controller.
        """
        self.controller = controller

    def reset_progress(self):
        """Reset the current progress property to 0.

        Note that when called, this function notifies the controller to update UI.
        """
        self.progress = 0
        self.controller.update_progress()

    def update_progress(self, progress: float):
        """Update the progress property.

        Note that when called, this function notifies the controller to update UI.

        Args:
            progress (float): Number between 0 and 1, with 0 meaning initiation (0%
                progress) and 1 meaning completion (100% progress).
        """
        if progress < 0:
            progress = 0
        elif progress > 1:
            progress = 1
        self.progress = progress
        self.controller.update_progress()

    def set_status(self, status: BotStatus):
        """Set the status property of the bot.

        Note that when called, this function notifies the controller to update UI.

        Args:
            status (BotStatus): Status to set the bot to.
        """
        self.status = status
        self.controller.update_status()

    def log_msg(self, msg: str, overwrite=False) -> None:
        """Send a message to the controller to be displayed in the log for the user.

        Args:
            msg (str): The message to log.
            overwrite (bool): If True, overwrites the current log message. If False,
                appends to the log.
        """
        msg = f"{debug.current_time()}: {msg}"
        self.controller.update_log(msg, overwrite)

    def clear_log(self):
        """Request the controller to tell the UI to clear the log."""
        self.controller.clear_log()

    # --- Mouse Utilities ---
    def move_mouse_randomly(self) -> None:
        """Move the cursor to a random location within the active game window.

        Note that the movements are limited to be within the active window associated
        with the `Bot` (e.g. Runelite).
        """
        xmin = self.win.window.topleft.x
        xmax = self.win.window.bottomright.x
        x = round(rd.biased_trunc_norm_samp(xmin, xmax))
        ymin = self.win.window.topleft.y
        ymax = self.win.window.bottomright.y
        y = round(rd.biased_trunc_norm_samp(ymin, ymax))
        self.mouse.move_to(Point(x, y), tween="easeInOutQuart")

    # --- Breaks and Waiting ---
    def sleep(self, lo: float = 0.1, hi: float = 0.3) -> None:
        """Don't do anything for a number of seconds.

        Note that there is a built-in skew to use times closer to the lower bound due
        to the definition of `biased_trunc_norm_samp`.

        Args:
            lo (float, optional): The lower bound for the truncated normal distribution
                the time to sleep in seconds will be drawn from. Defaults to 0.1.
            hi (float, optional): The upper bound for the truncated normal distribution
                the time to sleep in seconds will be drawn from. Defaults to 0.3.
        """
        if lo >= hi:
            self.log_msg("Lower bound must be less than upper bound to `sleep`.")
            raise ValueError
        time.sleep(rd.biased_trunc_norm_samp(lo, hi))

    def take_break(self, lo: int = 1, hi: int = 30, fancy: bool = False) -> None:
        """Take a break for a random amount of time.

        Args:
            lo (int): Minimum possible resting time.
            hi (int): Maximum possible resting time.
            fancy (bool): If True, the randomly generated value will be from a
                truncated normal distribution with randomly selected means in efforts
                to produce more human-like randomness.
        """
        self.log_msg("Taking a break...")
        if fancy:
            length = rd.biased_trunc_norm_samp(lo, hi)
        else:
            length = rd.trunc_norm_samp(lo, hi)
        length = round(length)
        for i in range(length):
            self.log_msg(
                f"Taking a break... {int(length) - i} seconds left.", overwrite=True
            )
            time.sleep(1)
        self.log_msg(f"Took {length} second break.", overwrite=True)

    def potentially_take_a_break(self, prob: float = 0.02) -> bool:
        """Potentially take a break to simulate human-like behavior.

        Allow for a percentage chance to take a break between activities.

        Args:
            prob (float, optional): Probability of taking a break. Defaults to 2%.

        Returns:
            bool: True if a break was taken, False otherwise.
        """
        prob_break = abs(random.normalvariate(prob, prob / 10))
        if rd.random_chance(prob=prob_break):
            # Simulate how a mouse might move when getting up or selecting another app.
            for _ in range(random.randint(1, 3)):
                self.move_mouse_randomly()
                self.sleep()
            # After moving, start the break.
            self.take_break(hi=self.break_max, fancy=False)
            return True
        return False
