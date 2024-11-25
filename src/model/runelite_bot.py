import random
import re
import shutil
import threading
import time
from abc import ABCMeta
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple, Union

import cv2
import numpy as np
import pyautogui as pag
import pytweening
import requests
from matplotlib.pyplot import imsave
from skimage.metrics import structural_similarity as ssim

import utilities.ocr as ocr
import utilities.random_util as rd
from model.bot import Bot
from model.runelite_window import RuneLiteWindow
from model.window import Window
from utilities import settings
from utilities.color_util import Color, ColorPalette, isolate_colors, isolate_contours
from utilities.extract_contours import extract_contours
from utilities.geometry import Point, Rectangle, RuneLiteObject, cosine_similarity
from utilities.img_search import BOT_IMAGES, search_img_in_rect


class RuneLiteBot(Bot, metaclass=ABCMeta):
    """The `RuneLiteBot` class contains bot methods specific to RuneLite (i.e. OSRS).

    Note that by specifying `metaclass=ABCMeta`, the `RuneLiteBot` class becomes an
    abstract base class. This means that `RuneLiteBot` contains ABSTRACT methods that
    MUST be implemented by a concrete subclass. Put another way, the abstract methods
    in `RuneLiteBot` cannot be instantiated directly, but must be called from a
    subclass.
    """

    win: RuneLiteWindow = None  # Every `RuneLiteBot` runs in a `RuneLiteWindow`.
    cp = ColorPalette()  # Defining here allows for default kwarg colors in type hints.

    # The game tick serves as the fundamental time unit within OSRS servers,
    # representing the duration of one server cycle. Analogous to a game's frame rate,
    # it dictates the pace of in-game events and actions. Data is transmitted to the
    # server in discrete chunks accumulated over the duration of a game tick. The
    # actual game tick is 0.6 seconds, but 3 additional milliseconds are included to
    # account for latency.
    game_tick = 0.603

    def __init__(
        self,
        game_title: str,
        bot_title: str,
        description: str,
        window: Window = RuneLiteWindow(f"RuneLite - {settings.get('username')}"),
    ) -> None:
        """Initialize a `RuneLiteBot`, a `Bot` tailored to Old School Runescape.

        Args:
            game_title (str): Title of the game the bot will interact with.
            bot_title (str): Title of the bot to display in the UI.
            description (str): Description of the bot to display in the UI.
            window (Window): Window object the bot will use to interact with the game
                client. Defaults to RuneLiteWindow("RuneLite - USERNAME").
        """
        super().__init__(game_title, bot_title, description, window)
        self.num_relogs = 0  # How many times we have logged in and out of RuneLite.

    # --- OCR ---
    def get_mouseover_text(
        self,
        contains: Union[str, List[str]] = None,
        colors: Union[Color, List[Color]] = None,
    ) -> Union[bool, str]:
        """Examine the mouseover text area.

        Args:
            contains (Union[str, List[str]], optional): The case-sensitive text to
                search for (single word, phrase, or list of words). If left blank,
                returns all text in the mouseover area.
            colors (Union[Color, List[Color]], optional): The color(s) to isolate. If
                left blank, isolates all expected colors. Consider using `OFF_<NAME>`
                colors (i.e. colors within a range) for best results.
        Returns:
            Union[bool, str]: True if a keyword was provided and an exact string was
                found, False if the provided keyword was not found. If no keywords were
                provided, all text in the mouseover area is returned, no spaces.
        """
        if colors is None:
            colors = [self.cp.bgr.OFF_WHITE_TEXT, self.cp.bgr.OFF_CYAN_TEXT]
        if contains is None:
            return ocr.scrape_text(self.win.mouseover, ocr.BOLD_12, colors)
        return bool(ocr.find_textbox(contains, self.win.mouseover, ocr.BOLD_12, colors))

    def get_chatbox_text(
        self,
        contains: str = None,
        colors: Union[Color, List[Color]] = None,
    ) -> Union[bool, str]:
        """Examine the entire chatbox for specific text existence, or scrape all text.

        Args:
            contains (str): The case-sensitive text to search for (single word or
                phrase). If left blank, returns all text in the chatbox.
            colors (Union[Color, List[Color]], optional): The color(s) to isolate. If
                left blank, isolates all expected colors. Consider using `OFF_<NAME>`
                colors (i.e. colors within a range) for best results.
        Returns:
            bool: True if exact string is found, False otherwise.
            str: If args are left blank, all text in the chatbox is returned.
        """
        if colors is None:
            colors = [self.cp.bgr.BLACK, self.cp.bgr.OFF_RED_TEXT]
        if contains is None:
            return ocr.scrape_text(self.win.chat, ocr.PLAIN_12, colors)
        return bool(ocr.find_textbox(contains, self.win.chat, ocr.PLAIN_12, colors))

    def get_chat_input_text(self) -> str:
        """Scrape the text on the chat input line.

        Returns:
            str: The input text without spaces.
        """
        return ocr.scrape_text(
            self.win.chat_input,
            ocr.PLAIN_12,
            self.cp.bgr.BLACK,
        )

    def close_active_chat_cursor(self) -> bool:
        """Closes the active chat cursor so keystrokes do not appear in chat input.

        This function is intended to return the chat input line to its default state:
            <username>: Press Enter to Chat...

        Returns:
            bool: True if the active chat cursor was closed, False otherwise.
        """
        self.log_msg("Closing active chat cursor...")
        if "pressentertochat" in self.get_chat_input_text().lower():
            self.log_msg("Chat cursor is not active.", overwrite=True)
            return True
        self.mouse.move_to(self.win.chat.random_point())
        self.mouse.click()
        pag.press("enter")  # Submit any lingering text.
        self.sleep()
        for _ in range(15):
            if "pressentertochat" in self.get_chat_input_text().lower():
                self.log_msg("Closed active chat cursor.", overwrite=True)
                return True
            pag.press("delete")
            self.sleep()
        return False

    def is_player_doing_action(self, action: str) -> bool:
        """Check whether the player character is performing a given action.

        This method checks the text in the `current_action` region of the chat window.

        Args:
            `action` (str): The case-sensitive action to check for (e.g. "Fishing").

        Returns:
            True if the player is performing the given action, False otherwise.
        """
        txt_box = ocr.find_textbox(
            action, self.win.current_action, ocr.PLAIN_12, self.cp.bgr.GREEN
        )
        return bool(txt_box)

    def get_update_text(self) -> str:
        """Get the automatically-generated black update text from the chat window.

        This method checks text in the `chat_history` region of the chat window.

        Returns:
            str: The most recent black update text, if there is black update text as
                the most recent line of text, otherwise an empty string.
        """
        return ocr.scrape_text(
            self.win.chat_history[0],
            ocr.PLAIN_12,
            self.cp.bgr.BLACK,
        )

    def get_chat_history(self, colors: Union[Color, List[Color]] = None) -> List[str]:
        """Get the chat history as an ordered list of strings.

        Note that each line of chat history contains no newlines nor spaces.

        Args:
            colors (Union[Color, List[Color]], optional): The color(s) of the text to
                scrape. Defaults to `BLACK` and `OFF_RED_TEXT`.

        Returns:
            List[str]: An ordered list of strings of the chat history.
        """
        lines = []
        if not colors:
            colors = [
                self.cp.bgr.BLACK,
                self.cp.bgr.OFF_RED_TEXT,
            ]
        colors = [colors] if colors and not isinstance(colors, list) else colors
        for chat_line in self.win.chat_history:
            txt = ocr.scrape_text(chat_line, ocr.PLAIN_12, colors=colors)
            lines.append(txt if txt else "")
        return lines

    def get_idle_notifier_text(self) -> str:
        """Get the Idle Notifier plug-in off-red update text from the chat window.

        This method checks text in the `chat_history` region of the chat window and
        assumes that the Idle Notifier RuneLite plug-in is installed and appropriately
        configured.

        Returns:
            str: The most recent off-red update text derived from the Idle Notifier
                RuneLite plug-in (if there is off-red update text as the most recent
                line of text), otherwise an empty string.
        """
        return ocr.scrape_text(
            self.win.chat_history[0],
            ocr.PLAIN_12,
            self.cp.bgr.OFF_RED_TEXT,
        )

    def check_idle_notifier_status(
        self,
        status: Literal["is_idle", "out_of_combat", "stopped_moving", "logout_soon"],
    ) -> bool:
        """Check if the Idle Notifier plug-in off-red text represents a given status.

        This method checks text in the `chat_history` region of the chat window and
        assumes that the Idle Notifier RuneLite plug-in is installed and appropriately
        configured. After scraping the text, it checks if it contains a phrase that
        corresponds to the given `status`.

        Args:
            status (Literal["is_idle", "out_of_combat", "stopped_moving",
                "logout_soon"]): The type of status to check for.

        Returns:
            bool: Whether the most recent Idle Notifier update text represents the
                given `status`.
        """
        codex = {
            "is_idle": "youarenowidle",
            "out_of_combat": "youarenowoutofcombat",
            "stopped_moving": "youhavestoppedmoving",
            "logout_soon": "abouttologoutfromidling",
        }
        text = self.get_idle_notifier_text().lower()
        return codex[status] in text

    # --- Object Detection ---
    def __seach_all_marked_obj_orders(
        self, color: Color, order_max: int = 3, req_txt: Union[str, List[str]] = None
    ) -> bool:
        """Incrementally search for objects up to a certain number of objects away.

        Since we may have accidentally hovered over a phantom object, we change the
        camera view and then search up to `order_max` potential phantoms nearby.

        Args:
            color (Color): The OpenCV-style HSV `Color`-tagged object to search for.
            order_max (int, optional): The maximum number of objects away to search
                for. Defaults to 3.
            req_txt (Union[str, List[str]], optional): The required mouseover text for
                an object to be valid. Defaults to None.

        Returns:
            bool: True if the object was found (and the mouse was moved), else False.
        """
        for order in range(order_max):
            _s = "" if order == 0 else "s"
            msg = (
                f"Retrying search for {color.name} {req_txt} objects up to"
                f" ({order + 1}) object{_s} away..."
            )
            self.log_msg(msg, overwrite=True)
            self.sleep()
            if self.move_mouse_to_color_obj(color=color, order=order, verbose=False):
                if req_txt:
                    if self.get_mouseover_text(contains=req_txt):
                        self.log_msg(
                            f"Found! Mouse moved to {color.name} {req_txt} object."
                        )
                        return True
                    continue
                return True
        msg = f"Failed ordered object search for {color.name} {req_txt} object."
        self.log_msg(msg, overwrite=True)
        return False

    def find_colors(
        self, rect: Rectangle, colors: Union[Color, List[Color]]
    ) -> List[RuneLiteObject]:
        """Get all contours on screen of a given HSV color as a list of rectangles.

        Note that a `RuneLiteObject` is effectively a 2D geometric shape bounded by a
        rectangle. Also, note that `find_colors` is one of the most important in the
        entire codebase, as nearly all `OSRSBot` objects are fundamentally designed to
        interface with the game window via color detection.

        Args:
            rect (Rectangle): A reference to the `Rectangle` that this shape belongs in
                (e.g., `Bot.win.control_panel`).
            colors (Union[Color, List[Color]]): The OpenCV-style HSV color tuple to
                search for.

        Returns:
            List[RuneLiteObject]: A list of `RuneLiteObject` objects or an empty
                list if none with a matching color were found.
        """
        img_bgr = rect.screenshot()
        isolated_contours = isolate_contours(img_bgr, colors)  # Threshold contours.
        objs = extract_contours(isolated_contours)  # Get each contour as a `Rectangle`.
        for obj in objs:
            obj.set_rectangle_reference(rect)
        return objs

    def find_sprite(
        self,
        win: Rectangle,
        png: Union[Path, str],
        folder: Union[Path, str] = "",
        confidence: float = 0.15,
        num_retries: int = 1,
        verbose=False,
    ) -> Optional[Rectangle]:
        """Get the sub-rectangle within a bounding rectangle that contains a sprite.

        Note that this functions as a wrapper for `search_img_in_rect`. Note
        additionally the RGB-formatted PNG templates have better performance than
        BGR-formatted ones!

        [TO DEV] Investigate why RGB-formatted template images (take from manual
        screenshots) perform better here despite the docs stipulating that OpenCV uses
        BGR-formatted images.

        Args:
            win (Rectangle): The bounding rectangle to search within.
            png (Union[Path, str]): The PNG filename of the sprite. The PNG should have
                no iCCP profile.
            folder (Union[Path, str], optional): The subfolder within the src/img/bot
                directory that contains PNG image.
            confidence (float, optional): The acceptable confidence level of reporting a
                match (i.e. p-value), ranging from 0 to 1, where 0 is a perfect match.
                Defaults to 0.15.
            num_retries (int, optional): The number of retries to perform. Defaults to
                10. Note that with every retry, confidence is incremented by 0.01 to
                improve the probability of a match.
            verbose (bool, optional): Whether to print a log message. Defaults to False.

        Returns:
            Optional[Rectangle]: Region where a sprite resides. None if not found.
        """
        folder = Path(folder) if isinstance(folder, str) else folder
        png_path = folder / png if folder else png
        sprite = search_img_in_rect(
            img=BOT_IMAGES / png_path,
            rect=win,
            confidence=confidence,
            num_retries=num_retries,
        )
        Not = "" if sprite else "Not"
        msg = f"{Not} found: {png_path.name}".lstrip().capitalize()
        if verbose:
            self.log_msg(msg)
        return sprite

    # --- Camera and Perspective ---
    def _compass_right_click(self, rel_y) -> None:
        """Right-click the compass icon, then move vertically to select a direction.

        Note that this function assumes the mouse is already hovering over the compass
        orb on the minimap.

        Args:
            rel_y (int): Amount of pixels to move below the current position of the
                mouse cursor. Note that a positive value moves the cursor DOWN.
        """
        self.mouse.move_to(self.win.compass_orb.random_point())
        self.mouse.right_click()
        self.mouse.move_rel(0, rel_y, 5, 2)
        self.mouse.click()

    def _export_compass_map(self, lo: int = 0, hi: int = 360) -> None:
        """For each degree of compass rotation within range, export a screenshot.

        This function captures a screenshot for every degree of rotation around the
        compass, starting from `lo` degrees (north by default) and proceeding
        clockwise. Each screenshot represents the view at a specific degree of compass
        rotation.

        Note that compass rotation is measured clockwise from the north (0 degrees),
        and screenshots are taken from `lo` degrees to `hi` degrees, inclusive.

        Args:
            lo (int, optional): Lower bound for the degree range. Defaults to 0.
            hi (int, optional): Upper bound for the degree range. Defaults to 360.
        """
        mode = self.win.mode  # Either "fixed_classic" or "resizable_classic".
        img_folder = BOT_IMAGES / "ui_templates" / "compass_degrees" / mode
        img_folder.mkdir(exist_ok=True, parents=True)
        for deg in range(lo, hi):
            self.log_msg(f"{deg}")
            if 0 <= deg < 45 or 315 <= deg < 360:
                self.set_compass_direction("north")
                ref_angle = 0 if deg < 45 else 360
            elif 45 <= deg < 135:
                self.set_compass_direction("west")
                ref_angle = 90
            elif 135 <= deg < 225:
                self.set_compass_direction("south")
                ref_angle = 180
            elif 225 <= deg < 315:
                self.set_compass_direction("east")
                ref_angle = 270
            rot = deg - ref_angle
            self.move_camera(horizontal=rot) if rot != 0 else None
            img_path = img_folder / f"{deg}.png"
            imsave(img_path, self.win.compass_orb.screenshot())

    def _load_compass_map(self) -> Dict[int, np.ndarray]:
        """Load compass images for each degree (0-359) into a dictionary.

        This method initializes the `_compass_map` attribute, which is a dictionary
        where each key is an integer degree (0-359) and each value is the corresponding
        compass image loaded from disk.

        The images are loaded with the `cv2.IMREAD_COLOR` flag to retain their
        original properties, excluding any alpha channels.

        Returns:
            Dict[int, np.ndarray]: Dictionary mapping integer degrees to corresponding
                compass images loaded from disk.
        """
        compass_map = {}
        mode = self.win.mode  # Either "fixed_classic" or "resizable_classic".
        img_folder = BOT_IMAGES / "ui_templates" / "compass_degrees" / mode
        for deg in range(360):
            img_path = img_folder / f"{deg}.png"
            compass_map[deg] = cv2.imread(str(img_path), cv2.IMREAD_COLOR)
        return compass_map

    def get_compass_angle(self) -> int:
        """Get the on-screen compass's degree of clockwise rotation from north.

        This method captures the current compass image from the screen and compares
        it against pre-loaded reference images for each degree (0-359) using the
        Structural Similarity Index (SSIM). The degree with the highest similarity
        score is considered the current orientation of the compass.

        If the `_compass_map` attribute is not already loaded, it `_load_compass_map`
        is called to initialize it.

        Returns:
            int: The degree (0-359) that best matches the current compass image.
        """
        cardinal_directions = {0, 90, 180, 270}
        if not hasattr(self, "_compass_map"):
            self._compass_map = self._load_compass_map()
        img_current = self.win.compass_orb.screenshot()

        def __compare_images(degree: int) -> Tuple[float, int]:
            img_ref = self._compass_map[degree]
            # `channel_axis` is the axis representing color channels in the img arrays.
            similarity = ssim(img_current, img_ref, channel_axis=2)
            return similarity, degree

        def __distance_to_cardinal(degree: int) -> int:
            return min(abs(degree - cd) for cd in cardinal_directions)

        # Parallelize the comparison of the current to reference image for each degree.
        with ThreadPoolExecutor() as executor:
            results = list(executor.map(__compare_images, range(360)))

        # Get all degrees with the maximum similarity score.
        max_similarity = max(results, key=lambda x: x[0])[0]
        max_results = [result for result in results if result[0] == max_similarity]
        # Get any cardinal directions in `max_results`.
        cardinal = next((x for x in max_results if x[1] in cardinal_directions), None)
        if cardinal:
            chosen_result = cardinal
        else:
            # Choose the largest degree with the min distance to any cardinal direction.
            chosen_result = max(
                max_results, key=lambda x: (-x[1], __distance_to_cardinal(x[1]))
            )
        return chosen_result[1]

    def set_compass_direction(
        self, direction: Literal["north", "east", "south", "west"]
    ):
        """Orient the game window and minimap to the given cardinal direction.

        Args:
            direction (Literal["north", "east", "south", "west"]): The cardinal
                direction to align the compass to.
        """
        self.log_msg(f"Setting compass {direction}...")
        self.mouse.move_to(self.win.compass_orb.random_point())
        if direction == "north":
            self.mouse.click()
        elif direction == "east":
            self._compass_right_click(43)
        elif direction == "south":
            self._compass_right_click(57)
        elif direction == "west":
            self._compass_right_click(72)
        self.log_msg(f"Set compass {direction}.", overwrite=True)

    def zoom(
        self,
        out: bool = True,
        percent_zoom: float = 1.0,
        minimap: bool = False,
        max_steps: int = 50,
        step_duration: float = 0.01,
        verbose: bool = True,
        overwrite: bool = True,
    ) -> None:
        """Zoom in or out on the game window or minimap.

        Note that 3600 is the amount of backward scrolling necessary to zoom all the
        way out from a fully-zoomed-in game window.

        Args:
            out (bool, optional): Zoom out if True, zoom in if False. Defaults to True.
            percent_zoom (float, optional): How much to zoom. Defaults to 1.0 (100%).
            minimap (bool, optional): Zoom the minimap if True, otherwise zoom the game
                window. Defaults to False.
            max_steps (int, optional): The maximum number of scroll steps to use. Use
                this to calibrate overall scroll animation speed. Defaults to 50.
            step_duration (float, optional): Seconds to wait between scroll steps. Use
                this to calibrate overall scroll animation speed. Defaults to 0.01.
            verbose (bool, optional): Whether to print log messages. Defaults to True.
            overwrite (bool, optional): Whether to reduce log message spam. Defaults to
                True.
        """
        # We can only zoom via scroll if the cursor is on the game window or minimap.
        win_obj = self.win.minimap if minimap else self.win.game_view
        win_str = "minimap" if minimap else "game window"
        zstyle = "out" if out else "in"
        if verbose:
            self.log_msg(f"Moving mouse to {win_str}...")
        self.mouse.move_to(win_obj.random_point())
        if verbose:
            self.log_msg(f"Mouse moved to {win_str}.", overwrite=overwrite)
        self.sleep()
        perc_str = int(percent_zoom * 100)
        if verbose:
            self.log_msg(
                f"Zooming {win_str} {zstyle} ({perc_str:d}%)...", overwrite=overwrite
            )
        max_zoom_units = 3600
        sign = -1 if out else 1
        scroll_amount = int(np.ceil(max_zoom_units * percent_zoom))
        num_steps = int(np.ceil(max_steps * percent_zoom))
        scroll_per_step = int(np.ceil(scroll_amount / num_steps)) * sign
        sleep_per_step = abs(step_duration / max_zoom_units)
        # Humans scroll with the mouse in short bursts.
        num_steps_scroll_burst = num_steps // random.randint(3, 5)
        for step in range(num_steps):
            if step % num_steps_scroll_burst == 0:
                self.sleep(0.3, 0.4)
            pag.scroll(scroll_per_step)
            self.sleep(sleep_per_step, 1.5 * sleep_per_step)
        if verbose:
            self.log_msg(
                f"Zoomed {zstyle} {win_str} ({perc_str:d}%).", overwrite=overwrite
            )

    def zoom_everything_out_completely(
        self,
        verbose: bool = False,
    ) -> None:
        """Zoom out the game view and minimap to their maximum extent.

        Args:
            verbose (bool, optional): Whether to print detailed log messages. Defaults
                to False.
        """
        self.log_msg("Zooming out game view and minimap all the way...")
        self.zoom(out=True, verbose=verbose)
        self.zoom(out=True, minimap=True, verbose=verbose)
        self.log_msg("Game view and minimap zoomed out 100%.", overwrite=True)

    def reset_minimap_zoom(self) -> None:
        """Reset the minimap zoom level to its default, where 4 pixels is 1 tile."""
        self.log_msg("Resetting minimap to default zoom level...")
        self.mouse.move_to(self.win.minimap.random_point())
        self.mouse.right_click()
        self.log_msg("Minimap reset to default zoom level.", overwrite=True)
        self.sleep()

    def move_camera(self, horizontal: int = 0, vertical: int = 0) -> None:
        """Rotate the camera by specified degrees horizontally or vertically (or both).

        Note that negative horizontal values rotate the camera left, and negative
        vertical values rotate the camera down.

        Args:
            horizontal (int, optional): The degree to rotate the camera (-360 to 360).
            vertical (int, optional): The degree to rotate the camera up (-90 to 90).

        Raises:
            ValueError: To move, the camera needs a nonzero amount of rotation in at
                least one direction.
            ValueError: Horizontal rotation is limited to -360 to 360 degrees.
            ValueError: Vertical rotation is limited to -90 to 90 degrees.
        """
        if horizontal == 0 and vertical == 0:
            msg = (
                "Must provide a nonzero amount of vertical or horizontal rotation"
                " (or both) to rotate the camera."
            )
            raise ValueError(msg)
        if horizontal < -360 or horizontal > 360:
            raise ValueError("Horizontal rotation is limited to -360 to 360 degrees.")
        if vertical < -90 or vertical > 90:
            raise ValueError("Vertical rotation is limited to -90 to 90 degrees.")

        # `Fraction` objects are used here to maintain high precision.
        rot_time_h = Fraction(35626031001, int(1e10))  # Secs to rot 360 deg horz.
        rot_deg_h = Fraction(360)  # Seconds to rotate 90 degrees vertically.
        horizontal = Fraction(horizontal)

        rot_time_v = Fraction(175, 100)
        rot_deg_v = Fraction(90)
        vertical = Fraction(vertical)

        # Define arrow key holding times.
        sleep_h = float(rot_time_h / rot_deg_h * abs(horizontal))
        sleep_v = float(rot_time_v / rot_deg_v * abs(vertical))

        direction_h = "left" if horizontal < 0 else "right"
        direction_v = "down" if vertical < 0 else "up"

        def keypress(direction: Literal["left", "right"], duration: float):
            pag.keyDown(direction, _pause=False)
            time.sleep(duration)
            pag.keyUp(direction, _pause=False)

        # Skip threading if the movement is not a combo of both vertical and horizontal.
        if vertical != 0 and horizontal == 0:
            keypress(direction_v, sleep_v)
        elif horizontal != 0 and vertical == 0:
            keypress(direction_h, sleep_h)
        else:
            thread_h = threading.Thread(
                target=keypress, args=(direction_h, sleep_h), daemon=True
            )
            thread_v = threading.Thread(
                target=keypress, args=(direction_v, sleep_v), daemon=True
            )
            delay = rd.biased_trunc_norm_samp(0, max(sleep_h, sleep_v))
            if sleep_h > sleep_v:
                thread_h.start()
                time.sleep(delay)
                thread_v.start()
            else:
                thread_v.start()
                time.sleep(delay)
                thread_h.start()
            thread_h.join()
            thread_v.join()

    def search_with_camera(
        self, theta: int = None, phi: int = None, verbose: bool = False
    ) -> None:
        """_summary_

        Args:

            verbose (bool, optional): _description_. Defaults to False.
        """
        """Rotate the camera with a reasonable degree of randomness.

        Args:
            theta (int, optional): The amount of horizontal rotation in degrees. If
                None, the amount is uniformly drawn from between 80 and 100 degrees.
            phi (int, optional): The amount of horizontal rotation in degrees. If
                None, the amount is uniformly drawn from between 10 and 50 degrees.
            verbose (bool, optional): Whether to print detailed camera movement logs.
            Defaults to False.
        Returns:
            bool: True if success, False otherwise.
        """
        if theta is None:
            theta = random.choice([-1, 1]) * random.uniform(80, 100)  # Degrees.
        if phi is None:
            phi = random.choice([-1, 1]) * random.uniform(20, 70)
        self.move_camera(horizontal=theta, vertical=phi)
        if verbose:
            msg = f"Camera adjusted by ({theta:5.1f}, {phi:4.1f})."
            self.log_msg(msg)

    def pitch_down_and_align_camera(
        self,
        direction: str = "north",
    ) -> None:
        """Orient our camera perspective in a standardardized way.

        Note that despite `move_camera` taking a positive argument, when a camera is
        pitched down, it means that the viewing angle is tilted downward, and that is
        exactly what we are trying to do when we want to get a bird's-eye view of our
        character.

        Args:
            direction (str, optional): Cardinal direction. Defaults to "north". Other
            options are "west", "east", and "south".
        """
        self.log_msg("Initializing camera orientation...")
        self.move_camera(vertical=90)
        self.log_msg("Camera pitched down 90 degrees.", overwrite=True)
        self.sleep()
        self.set_compass_direction(direction)

    # --- Movement ---
    def walk_to_random_point_nearby(
        self,
        verbose: bool = True,
        overwrite: bool = True,
    ) -> bool:
        """Walk to a random point nearby.

        Args:
            verbose (bool, optional): Whether to print log messages. Defaults to True.
            overwrite (bool, optional): Whether to reduce log message spam. Defaults to
                True.

        Returns:
            bool: True if we initiated the walk successfully, False if we didn't.
        """

        x_min = self.win.game_view.top_left.x
        x_max = self.win.game_view.top_right.x
        y_min = self.win.game_view.top_right.y
        y_max = self.win.game_view.bottom_right.y
        center = self.win.game_view.center
        xpad = round((x_max - x_min) / 4)
        ypad = round((y_max - y_min) / 4)
        if verbose:
            self.log_msg("Moving to a random point nearby...")
        start = time.time()
        timeout = 20
        while (time.time() - start) < timeout:
            self.mouse.move_to(rd.random_point_around(center, xpad, ypad))
            self.mouse.click()
            p0 = self.get_world_point()
            self.sleep(3, 4)
            if self.get_world_point() != p0:
                if verbose:
                    self.log_msg("Moved to a random point nearby.", overwrite=overwrite)
                return True
            continue
        self.log_msg("Failed to walk to a random point nearby.", overwrite=overwrite)
        return False

    def walk_along_highlighted_path(
        self,
        v0: str,
        dest_color: Color,
        dest_req_txt: str,
        path_color: Color = cp.hsv.RED_PATH,
        duration: float = 10,
    ) -> bool:
        """Walk a highlighted path laid out by the Shortest Path RuneLite plug-in.

        Note that the orientation of the camera is supposed to align with the direction
        of travel along the path when this function is called. Remember that (0, 0) is
        the upper left corner of the game view and (x, y) is the lower right. The
        origin is defined in this way to coincide with how most click-and-drag
        rectangles are dragged down and to the right.

        Args:
            v0 (str): The cardinal direction to align to before embarking. Choose
                between "north", "west", "east", and "south".
            dest_color (Color): The color of the destination marker as defined in
                `api.colors_hsv`.
            dest_req_txt (str): The required mouseover text for a potential destination
                marker to be valid.
            path_color (Color, optional): The color of the path marked on the game
                view (as defined in `api.colors_hsv`). Defaults to cp.hsv.RED_PATH.
            duration (float, optional): The amount of seconds to follow the path before
                ceasing to issue additional click commands. Defaults to 10.

        Returns:
            bool: True if we walked the path and got close enough to our destination,
                False otherwise.
        """
        self.pitch_down_and_align_camera(v0)
        center = self.win.game_view.center
        x0, y0 = center
        v0 = ((x0 + 0) - x0, (y0 - 1) - y0)  # Unit vector pointing vertically.
        self.mouse.move_to(center)
        start = time.time()
        close_enough = False
        while not close_enough and time.time() - start < duration:
            local_rect = self.mouse.get_rect_around_point(center, pad=150)
            tiles = self.find_colors(rect=local_rect, colors=path_color)
            tiles = sorted(tiles, key=RuneLiteObject.dist_from_rect_center)
            similarities = {}
            for tile in tiles:
                tile_center = tile._center
                xf, yf = tile_center[0], tile_center[1]
                vec = (xf - x0, yf - y0)
                similarities[tile] = (vec, cosine_similarity(v0, vec))
            chosen_tile = max(similarities, key=lambda k: similarities[k][1])
            v0 = similarities[chosen_tile][0]
            self.mouse.move_to(chosen_tile.random_point(), mouseSpeed="medium")
            self.right_click_select_context_menu("Walk here")
            self.sleep(0.5, 1.0)
            close_enough = self.find_and_mouse_to_marked_object(
                color=dest_color, req_txt=dest_req_txt, num_retries=2
            )
            if close_enough:
                self.mouse.click()
                return True
        return False

    # --- Mouse Utilities ---
    def right_click_select_context_menu(
        self,
        req_txt: str,
        pad: int = 120,
        font: ocr.FontDict = ocr.BOLD_12,
        color: Color = cp.bgr.WHITE_DROPDOWN_TEXT,
        exit_txt: str = None,
        exit_direction: Literal["up", "down", "left", "right"] = "up",
        screenshot: bool = False,
    ) -> bool:
        """Right-click to then select an option from the context menu.

        Note that the right-click is given at the current position of the mouse. The
        cursor also only moves vertically toward the target point after the right-click
        for reliability. If the cursor strays too far off the context menu, the menu
        disappears, and the function fails.

        Args:
            req_txt (str): The case-sensitive option text to select with a left-click.
            pad (int, optional): How much padding around the mouse cursor to use when
                drawing the context rectangle, measured in pixels. Defaults to 120.
            font (ocr.FontDict, optional): Font of the desired menu text option.
                Defaults to `ocr.BOLD_12`.
            color (Color, optional): Color of the desired menu text option. Defaults to
                `cp.bgr.WHITE_DROPDOWN_TEXT`.
            exit_txt (str, optional): If `exit_txt` is an available option, it will
                prompt the cursor to move away from context menu, cancelling the choice
                any left-click selection. Defaults to None.
            exit_direction (Literal["up", "down", "left", "right"]): If `exit_text` is
                detected, whether to move the cursor up, left, right, or down to
                dismiss the context menu.
            screenshot (bool, optional): Whether to take a picture of the context
                rectangle defined by the given padding. Using these images helps with
                calibration. Defaults to False.

        Returns:
            bool: True if text was detected and clicked, False otherwise.
        """
        posn = pag.position()
        self.mouse.right_click()
        self.sleep()  # A human takes a second to look at the options.
        # Draw a rectangle around where we just clicked to gather context.
        rc_rect = self.mouse.get_rect_around_point(posn, pad=pad)
        if screenshot:  # Using this is helpful for calibrating `pad`.
            filename = "context-menu.png"
            imsave(filename, rc_rect.screenshot())
            self.log_msg(f"Screenshot saved as: {filename}")
        if txt := ocr.scrape_text(
            rc_rect,
            font=ocr.BOLD_12,
            colors=self.cp.bgr.WHITE,
            exclude_chars=[char for char in ocr.PROBLEMATIC_CHARS if char != ","],
        ):
            txt = txt.lower()
            if exit_txt is not None:
                exit_txt = exit_txt.lower().replace(" ", "")
                pattern = rf"{exit_txt}\D"  # \D matches any non-digit character.
                if re.search(pattern, txt):
                    dx, dy = round(1.1 * pad), round(1.1 * pad)
                    dx = -dx if exit_direction == "left" else dx
                    dy = -dy if exit_direction == "up" else dy
                    (x, y) = (dx, 0) if exit_direction in ["left", "right"] else (0, dy)
                    self.mouse.move_rel(x, y)
                    return False
        if ocr_rect := ocr.find_textbox(req_txt, rc_rect, font=font, colors=color):
            # Note that if the mouse strays too far, the context menu will disappear.
            menu_point = ocr_rect[0].center  # Use the center for reliability.
            y_move = menu_point.y - posn.y  # Only move vertically.
            self.mouse.move_rel(0, y_move, dx=5, knotsCount=0, mouseSpeed="fastest")
            self.mouse.click()
            self.sleep()
            return True
        return False

    def move_mouse_to_color_obj(
        self,
        color: Color,
        order: int = 0,
        dist_measure: str = "absolute",
        verbose: bool = True,
    ) -> bool:
        """Move the mouse to a region of a specified color within the game window.

        This function is the main way the bot interacts with color-marked objects in
        the game. Remember that the color markers are set with the Object Markers
        RuneLite plug-in.

        Args:
            color (Color): One of the colors listed in `utilities.api.colors_hsv`.
            order (int, optional): Specify which object to move the mouse to by
                providing the desired index out of the list of detected objects ordered
                from closest to furthest from our character. Defaults to 0 (i.e.
                closest).
            dist_measure (str, optional): How the distance from our character to the
                object should be measured. Choose from "vertical", "horizontal" or
                "absolute". Defaults to "absolute".
            verbose (bool, optional): Whether to log detailed messages. Defaults to
                True.

        Returns:
            bool: True if the mouse was moved to the color-marked object, else False.
        """
        if match_obj := self.find_colors(self.win.game_view, color):
            key = RuneLiteObject.dist_from_rect_center
            if dist_measure == "vertical":
                key = RuneLiteObject.vert_dist_from_rect_center
            if dist_measure == "horizontal":
                key = RuneLiteObject.horz_dist_from_rect_center
            if dist_measure == "absolute":
                key = RuneLiteObject.dist_from_rect_center
            match_obj = sorted(match_obj, key=key)
            _s = "s" if len(match_obj) != 1 else ""
            if verbose:
                self.log_msg(f"{len(match_obj)} {color.name} object{_s} found.")
            if not order + 1 <= len(match_obj):
                order = 0  # If the order is invalid, use the closest instead.
            self.mouse.move_to(match_obj[order].random_point())
            if verbose:
                self.log_msg(f"Mouse moved to {color.name} object.", overwrite=True)
            return True
        return False

    def find_and_mouse_to_marked_object(
        self,
        color: Color,
        req_txt: Union[str, List[str]],
        req_txt_colors: Union[Color, List[Color]],
        num_retries: int = 10,
    ) -> bool:
        """After traveling within range, mouse to a color-marked object.

        Args:
            color (Color): The OpenCV-style HSV color tuple of the marker used to tag
                the object we are searching for.
            req_txt (Union[str, List[str]]): Required mouseover text for the object to
                be a match. It can be one case sensitive string, or any one of a given
                list of case sensitive strings.
            req_txt_colors (Union[Color, List[Color]]): OpenCV-style BGR color tuples
                to search for. `OFF_<NAME>` colors are good choices.
            num_retries (int, optional): The number of times to retry searching if the
                first search failed. Defaults to 10.

        Returns:
            bool: True if we moused to the object, False if not.
        """
        self.move_mouse_to_color_obj(color=color)
        found_obj = self.get_mouseover_text(contains=req_txt, colors=req_txt_colors)
        _not = "" if found_obj else " not"
        self.log_msg(f"{color.name}-marked {req_txt} object{_not} found.")
        if found_obj:
            return True
        if not found_obj:
            for i in range(num_retries):
                if self.__seach_all_marked_obj_orders(color, req_txt=req_txt):
                    return True
                if i >= int(0.3 * num_retries):
                    # Start by tilting a bit down to see if that does the trick.
                    self.move_camera(vertical=random.normalvariate(-20, 1))
                if i > int(0.5 * num_retries):
                    self.search_with_camera()  # Then rotate the camera a little.
                if i >= int(0.8 * num_retries):  # Over 80% retries, walk a bit too.
                    self.walk_to_random_point_nearby(verbose=False)
                if i % (num_retries // 2) == 0:
                    self.log_msg(
                        "Resetting minimap and game window zoom...", overwrite=True
                    )
                    self.zoom(minimap=True, verbose=False)
                    self.zoom(verbose=False)
                    self.log_msg("Minimap and game window zoom reset.", overwrite=True)
                self.log_msg(
                    f"Retrying marked object search ({i + 1}/{num_retries}).",
                    overwrite=True,
                )
        self.log_msg(
            f"Could not find {color.name}-marked object, even after retrying"
            f" ({num_retries} attempts)."
        )
        return False

    # --- Inventory ---
    def get_inv_item_slots(
        self, png: str, folder: str, confidence: float = 0.15
    ) -> List[int]:
        """Get the inventory indices containing the provided item sprite.

        Args:
            png (str): The PNG filename of the item sprite.
            folder (str): The subfolder under "./src/img/bot" containing `png`.
            confidence (float, optional): The acceptable confidence level of reporting a
                match (i.e. p-value), ranging from 0 to 1, where 0 is a perfect match.
                Defaults to 0.15.

        Returns:
            List[int]: A list of inventory slot indices where the given sprite was
                found, otherwise an empty list if no matches were found.
        """
        inds = []
        for i, slot in enumerate(self.win.inventory_slots):
            if self.find_sprite(
                win=slot, png=png, folder=folder, confidence=confidence
            ):
                inds.append(i)
        return inds

    def get_first_item_index(
        self, png: str, folder: str, confidence: float = 0.15
    ) -> Optional[int]:
        """Search the inventory for a sprite and return the first matching slot index.

        Args:
            png (str): The PNG filename of the item sprite.
            folder (str): The subfolder under "./src/img/bot" containing `png`.
            confidence (float, optional): The acceptable confidence level of reporting a
                match (i.e. p-value), ranging from 0 to 1, where 0 is a perfect match.
                Defaults to 0.15.

        Returns:
            Optional[int]: The slot number where the sprite was found, or None if the
                sprite wasn't found at all.
        """
        for i, slot in enumerate(self.win.inventory_slots):
            if self.find_sprite(
                win=slot, png=png, folder=folder, confidence=confidence
            ):
                return i

    def get_num_empty_inv_slots(self, verbose=False) -> int:
        """Determine how much space is left in our character's inventory.

        Args:
            verbose (bool, optional): Whether to print detailed log messages. Defaults
                to False.

        Returns:
            int: The number of empty spaces left our character's inventory.
        """
        # Determine whether each inventory slot is empty.
        item_path = BOT_IMAGES / "inventory" / "empty-slot.png"
        num_empty_slots = 0
        for i, slot in enumerate(self.win.inventory_slots):
            if search_img_in_rect(item_path, slot, confidence=0.10):
                if verbose:
                    self.log_msg(f"Inventory slot {i+1} is empty.")
                num_empty_slots += 1
        return num_empty_slots

    def get_num_full_inv_slots(self, verbose=False) -> int:
        """Determine the number of occupied slots in our character's inventory.

        Note that this is just a wrapper for `get_num_empty_inv_slots`.

        Args:
            verbose (bool, optional): Whether to print detailed log messages. Defaults
                to False.

        Returns:
            int: The number of occupied slots in our character's inventory.
        """
        return 28 - self.get_num_empty_inv_slots(verbose=verbose)

    def get_num_item_in_inv(
        self, png: str, folder: str, confidence: float = 0.15
    ) -> int:
        """Get the quantity of an item that may or may not be in our inventory.

        Args:
            png (str): The PNG filename of the item sprite.
            folder (str): The subfolder under "./src/img/bot" containing `png`.
            confidence (float, optional): The acceptable confidence level of reporting a
                match (i.e. p-value), ranging from 0 to 1, where 0 is a perfect match.
                Defaults to 0.15.

        Returns:
            int: The number of the given item found in our inventory.
        """
        num_found = 0
        for slot in self.win.inventory_slots:
            if self.find_sprite(
                win=slot, png=png, folder=folder, confidence=confidence
            ):
                num_found += 1
        return num_found

    def is_item_in_inv(self, png: str, folder: str, confidence: float = 0.15) -> bool:
        """Determine whether a specific item is in our character's inventory.

        Args:
            png (str): The name of the PNG image sprite to search for.
            folder (str): The subfolder under "./src/img/bot" containing `png`.
            confidence (float, optional): The acceptable confidence level of reporting a
                match (i.e. p-value), ranging from 0 to 1, where 0 is a perfect match.
                Defaults to 0.15.

        Returns:
            bool: True if the sprite is in our inventory, False otherwise.
        """
        item = self.find_sprite(
            win=self.win.inventory, png=png, folder=folder, confidence=confidence
        )
        return bool(item)

    def is_inv_full(self) -> bool:
        """Check whether our character's inventory is full.

        Returns:
            bool: True if we have a full backpack, False otherwise.
        """
        return self.get_num_empty_inv_slots() == 0

    def is_inv_empty(self) -> bool:
        """Check whether our character's inventory is empty.

        Returns:
            bool: True if we have an empty backpack, False otherwise.
        """
        return self.get_num_empty_inv_slots() == 28

    def inv_has_space(self) -> bool:
        """Check whether our character's inventory has space.

        Returns:
            bool: True if there is at least one empty slot in our backpack, else False.
        """
        return self.get_num_empty_inv_slots() > 0

    def inv_has_items(self) -> bool:
        num_full = 28 - self.get_num_empty_inv_slots()
        return num_full > 0

    def is_inv_slot_full(self, slot_ind: int) -> bool:
        """Determine whether or not a given inventory slot is full.

        Note that this method is intended to be used as a quick solution to check
        how our character's inventory fills up during gathering tasks like mining or
        chopping wood.

        Args:
            slot_ind (int): The slot index from 0 to 27 (inclusive).

        Returns:
            bool: True if the slot is full, False otherwise.
        """
        item_path = BOT_IMAGES / "inventory" / "empty-slot.png"
        empty_slot = search_img_in_rect(item_path, self.win.inventory_slots[slot_ind])
        state = "empty" if empty_slot else "full"
        self.log_msg(f"Inventory slot index {slot_ind} is {state}.")
        return state == "full"

    def get_inv_drop_traversal_path(self) -> List[int]:
        """Get a list of numbers corresponding to a common 7x4 grid traversal path.

        Note that the 7x4 backpack inventory is indexed like:
            [ 0,  1,  2,  3]\n
            [ 4,  5,  6,  7]\n
            [ 8,  9, 10, 11]\n
            [12, 13, 14, 15]\n
            [16, 17, 18, 19]\n
            [20, 21, 22, 23]\n
            [24, 25, 26, 27]

        Returns:
            List[int]: The chosen index traversal path, or a random choice from those
                available if a `kind` is not specified.
        """
        inds = list(range(28))
        left_right_snake = [
            item
            for i in range(0, len(inds), 8)  # Iteratate over `inds` in chunks of 8.
            for item in inds[i : i + 4] + inds[i + 4 : i + 8][::-1]
        ]
        down_up_snake = []
        for i in range(4):
            col = [i + 4 * j for j in range(7)]
            col = col if i % 2 == 0 else col[::-1]
            down_up_snake += col
        randomized = random.sample(inds, len(inds))
        standards = [inds, left_right_snake, down_up_snake, randomized]
        diag_top_left = (
            "0, 1, 4, 8, 5, 2, 3, 6, 9, 12, 16, 13, 10, 7, 11, 14, 17, 20, 24, 21, 18,"
            " 15, 19, 22, 25, 26, 23, 27"
        )
        diag_bot_left = (
            "24, 20, 25, 26, 21, 16, 12, 17, 22, 27, 23, 18, 13, 8, 4, 9, 14, 19, 15,"
            " 10, 5, 0, 1, 6, 11, 7, 2, 3"
        )
        diag_top_right = (
            "3, 7, 2, 1, 6, 11, 15, 10, 5, 0, 4, 9, 14, 19, 23, 18, 13, 8, 12, 17, 22,"
            " 27, 26, 21, 16, 20, 25, 24"
        )
        diag_bot_right = (
            "27, 23, 26, 25, 22, 19, 15, 18, 21, 24, 20, 17, 14, 11, 7, 10, 13, 16, 12,"
            " 9, 6, 3, 2, 5, 8, 4, 1, 0"
        )
        diags = [diag_top_left, diag_bot_left, diag_top_right, diag_bot_right]
        diags = [[int(num.strip()) for num in diag.split(",")] for diag in diags]
        traversals_base = diags + standards
        traversals_reversed = [sublist[::-1] for sublist in traversals_base]
        traversals_full = traversals_base + traversals_reversed
        return random.choice(traversals_full)

    def drop_items(self, slots: List[int], verbose: bool = True) -> None:
        """Left-click one or more inventory slots to drop items.

        This function relies on the Custom Left Click plug-in in RuneLite being
        configured correctly.

        Args:
            slots (List[int]): The list of inventory slot indices corresponding to
                where items we wish to drop reside.
            verbose (bool, optional): Whether to print relevant log messages. Defaults
                to True.
        """
        if verbose:
            self.log_msg("Dropping items...")
        for slot in slots:
            self.mouse.move_to(
                self.win.inventory_slots[slot].random_point(),
                mouseSpeed="fastest",
                knotsCount=1,
                offsetBoundaryY=40,
                offsetBoundaryX=40,
                tween=pytweening.easeOutBack,
            )
            self.mouse.click()

    def drop_all_items(self, skip_rows: int = 0, skip_slots: List[int] = None) -> None:
        """Individually left-click all items in the inventory to drop them.

        Note that this function requires the Custom Left Click Drop RuneLite plug-in be
        configured correctly for the items in our character's inventory we want to drop.

        Args:
            skip_rows (int): The number of rows to skip not drop, 0 corresponding to
                the top of the inventory row.
            skip_slots (List[int]): The indices of the inventory slots to skip left
                clicking.
        """
        self.log_msg("Dropping inventory...")
        # Determine the slots to skip.
        if skip_slots is None:
            skip_slots = []
        if skip_rows > 0:
            row_skip = list(range(skip_rows * 4))
            skip_slots = np.unique(row_skip + skip_slots)
        for i, slot in enumerate(self.win.inventory_slots):
            if i in skip_slots:
                continue
            self.mouse.move_to(
                slot.random_point(),
                mouseSpeed="fastest",
                knotsCount=1,
                offsetBoundaryY=40,
                offsetBoundaryX=40,
                tween=pytweening.easeInOutQuad,
            )
            self.mouse.click()

    # --- General Utilities ---
    def sleep_while_not_idle(self) -> None:
        """Do nothing while we wait for our character to finish their task."""
        while not self.check_idle_notifier_status("is_idle"):
            self.sleep()

    def sleep_while_moving(self):
        """Do nothing while we wait for our character to finish moving."""
        while not self.check_idle_notifier_status("stopped_moving"):
            self.sleep()

    def sleep_while_traveling(self):
        """Do nothing while we wait for our character to finish traveling."""
        while self.is_traveling():
            self.sleep()

    def friends_nearby(self) -> bool:
        """Check the minimap for green dots that indicate friends are nearby.

        Returns:
            bool: True if friends are nearby, False otherwise.
        """
        minimap_bgr = self.win.minimap.screenshot()
        minimap_hsv = cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2HSV)
        # Blacken out the minimap and turn friend dots white.
        only_friends = isolate_colors(minimap_hsv, [self.cp.hsv.GREEN])
        # Sum the elements in the image matrix and divide by the total number of
        # elements to calculate the mean "friendliness".
        mean = only_friends.mean(axis=(0, 1))
        return mean != 0.0

    def toggle_auto_retaliate(
        self, state: Literal["on", "off"], verbose: bool = True
    ) -> bool:
        """Toggle auto retaliate.

        Args:
            state (Literal["on", "off"]): Whether to turn auto retaliate on or off.
            verbose (bool, optional): Whether to show log messages. Defaults to True.

        Returns:
            bool: True if auto retaliate was toggled, False otherwise.
        """
        if verbose:
            self.log_msg(f"Attempting to toggle auto retaliate: {state}")
        self.mouse.move_to(self.win.cp_tabs[0].random_point())
        self.mouse.click()
        self.sleep()

        folderpath = BOT_IMAGES / "combat"
        filename = "autoretal-off.png" if state == "on" else "autoretal-on.png"
        filepath = folderpath / filename
        if btn := search_img_in_rect(filepath, self.win.cp_inner):
            self.mouse.move_to(btn.random_point(), mouseSpeed="fast")
            self.mouse.click()
            if verbose:
                self.log_msg(f"Toggle auto retaliate: {state}", overwrite=True)
            return True
        if verbose:
            self.log_msg(
                f"Toggle ignored. Auto retaliate is already {state}.", overwrite=True
            )
        return False

    def select_combat_style(self, combat_style: str) -> bool:
        """Select a combat style from the combat tab.

        Args:
            combat_style (str): The attack type. Choose between:
                ["accurate", "aggressive", "defensive", "controlled", "rapid",
                 "longrange"]

        Returns:
            bool: True if the combat style was selected, False otherwise.
        """
        # Ambiguous words are at the end of the keyword lists to be tried last.
        styles = {
            "accurate": [
                "Accurate",
                "Short fuse",
                "Punch",
                "Chop",
                "Jab",
                "Stab",
                "Spike",
                "Reap",
                "Bash",
                "Flick",
                "Pound",
                "Pummel",
            ],
            "aggressive": [
                "Kick",
                "Smash",
                "Hack",
                "Swipe",
                "Slash",
                "Impale",
                "Lunge",
                "Pummel",
                "Chop",
                "Pound",
            ],
            "defensive": ["Block", "Fend", "Focus", "Deflect"],
            "controlled": ["Spike", "Lash", "Lunge", "Jab"],
            "rapid": ["Rapid", "Medium fuse"],
            "longrange": ["Longrange", "Long fuse"],
        }
        if combat_style not in styles:
            raise ValueError(f"Invalid combat style: {combat_style}")
        # Click the combat tab.
        self.mouse.move_to(self.win.cp_tabs[0].random_point(), mouseSpeed="fastest")
        self.mouse.click()
        for style in styles[combat_style]:
            # Try to find the center of the word with OCR
            if result := ocr.find_textbox(
                style, self.win.cp_inner, ocr.PLAIN_11, self.cp.bgr.OFF_ORANGE_TEXT
            ):
                # If the word is found, draw a rectangle around it and click a random
                # point in that rectangle
                center = result[0].center
                rect = Rectangle.from_points(
                    Point(center[0] - 32, center[1] - 34),
                    Point(center[0] + 32, center[1] + 10),
                )
                self.mouse.move_to(rect.random_point(), mouseSpeed="fastest")
                self.mouse.click()
                self.log_msg(f"Combat style selected: {combat_style}")
                return True
        self.log_msg(f"Could not select combat style: {combat_style}")
        return False

    def toggle_run(self, state: Literal["on", "off"], verbose: bool = True) -> bool:
        """Toggle whether our character should run (on) or walk (off).

        Args:
            state (Literal["on", "off"]): The state to toggle to.
            verbose (bool, optional): Whether to show log messages. Defaults to True.

        Returns:
            bool: True if run was toggled (on or off), or False if the toggle command
                was redundant and therefore ignored.
        """
        if verbose:
            self.log_msg(f"Attempting to toggle run {state}...")
        folder = Path("ui_templates") / "orbs"
        png = "run-off.png" if state == "on" else "run-on.png"
        if btn := self.find_sprite(
            win=self.win.run_orb, png=png, folder=folder, confidence=0.10, verbose=False
        ):
            self.mouse.move_to(btn.random_point())
            self.mouse.click()
            if verbose:
                self.log_msg(f"Run toggled {state}.", overwrite=True)
            return True
        if verbose:
            self.log_msg(f"Toggle ignored. Run is already {state}.", overwrite=True)
        return False

    def toggle_run_on_if_enough_energy(
        self,
        min_energy: int = 25,
    ) -> bool:
        """If our character is above a certain energy level, toggle run on.

        Note that maximum run energy is 100.

        Args:
            min_energy (int, optional): Minimum energy required. Defaults to 25.

        Returns:
            bool: True if run was toggled, or False if the toggle command was redundant
                and therefore ignored, or because we didn't have the minimum required
                energy.
        """
        current_energy = self.get_run_energy()
        if current_energy >= min_energy:
            return self.toggle_run(state="on")
        msg = f"Not enough energy to toggle run on ({current_energy} < {min_energy})."
        self.log_msg(msg)
        return False

    def open_chat_tab(
        self,
        name: Literal["all", "game", "public", "private", "channel", "clan", "trade"],
    ) -> None:
        """Mouse to the given chat tab and left-click it to open it up.

        Note that the Report tab is not included here.

        Args:
            name (Literal["all", "game", "public", "private", "channel", "clan",
                "trade"]): The name of the tab.
        """
        try:
            ind = [
                "all",
                "game",
                "public",
                "private",
                "channel",
                "clan",
                "trade",
            ].index(name)
        except ValueError:
            ind = 1  # Use the Game tab as the default since it's the most informative.
        self.log_msg(f"Mousing to {name.capitalize()} chat tab...")
        self.mouse.move_to(self.win.chat_tabs[ind].random_point())
        self.mouse.click()
        self.log_msg(f"{name.capitalize()} chat tab selected.", overwrite=True)
        self.sleep()

    def is_chat_tab_open(
        self,
        name: Literal["all", "game", "public", "private", "channel", "clan", "trade"],
    ):
        """Check whether a given chat tab is currently open.

        Args:
            name (Literal["all", "game", "public", "private", "channel", "clan",
                "trade"]): The chat tab to check for whether it is open or not.
        """
        names = ["all", "game", "public", "private", "channel", "clan", "trade"]
        ind = names.index(name)
        folder = BOT_IMAGES / "ui_templates" / "chat_tabs" / "clicked"
        png = f"{ind}.png"
        match = self.find_sprite(
            win=self.win.chat_tabs_all, png=png, folder=folder, confidence=0.05
        )
        state = "open" if match else "closed"
        msg = f"{name.replace('_', ' ').capitalize()} tab is {state}."
        self.log_msg(msg)
        return state == "open"

    def open_control_panel_tab(
        self,
        name: Literal[
            "combat_options",
            "skills",
            "character_summary",
            "inventory",
            "worn_equipment",
            "prayer",
            "spellbook",
            "chat_channel",
            "friends_list",
            "account_management",
            "logout",
            "settings",
            "emotes",
            "music_player",
        ],
    ) -> None:
        """Mouse to the given control panel tab and left-click it to open it up.

        Args:
            name ( Literal["combat_options", "skills", "character_summary",
                "inventory", "worn_equipment", "prayer", "spellbook", "chat_channel",
                "friends_list", "account_management", "logout", "settings", "emotes",
                "music_player"]): The control panel tab to open.
        """
        names = [
            "combat_options",
            "skills",
            "character_summary",
            "inventory",
            "worn_equipment",
            "prayer",
            "spellbook",
            "chat_channel",
            "friends_list",
            "account_management",
            "logout",
            "settings",
            "emotes",
            "music_player",
        ]
        try:
            ind = names.index(name)
        except ValueError:
            ind = 3  # Default to inventory.
        self.log_msg(f"Mousing to {name} tab...")
        self.mouse.move_to(self.win.cp_tabs[ind].random_point())
        self.mouse.click()
        msg = f"{name.replace('_', ' ').capitalize()} tab selected."
        self.log_msg(msg, overwrite=True)
        self.sleep()

    def is_control_panel_tab_open(
        self,
        name: Literal[
            "combat_options",
            "skills",
            "character_summary",
            "inventory",
            "worn_equipment",
            "prayer",
            "spellbook",
            "chat_channel",
            "friends_list",
            "account_management",
            "logout",
            "settings",
            "emotes",
            "music_player",
        ],
    ):
        """Check whether a given control panel tab is currently open.

        Args:
            name ( Literal["combat_options", "skills", "character_summary",
                "inventory", "worn_equipment", "prayer", "spellbook", "chat_channel",
                "friends_list", "account_management", "logout", "settings", "emotes",
                "music_player"]): The control panel to check for whether it is open or
                not.
        """
        names = [
            "combat_options",
            "skills",
            "character_summary",
            "inventory",
            "worn_equipment",
            "prayer",
            "spellbook",
            "chat_channel",
            "friends_list",
            "account_management",
            "logout",
            "settings",
            "emotes",
            "music_player",
        ]
        ind = names.index(name)
        folder = BOT_IMAGES / "ui_templates" / "cp_tabs" / "clicked"
        png = f"{ind}.png"
        match = self.find_sprite(
            win=self.win.control_panel, png=png, folder=folder, confidence=0.05
        )
        state = "open" if match else "closed"
        msg = f"{name.replace('_', ' ').capitalize()} tab is {state}."
        self.log_msg(msg)
        return state == "open"

    def prepare_standard_initial_state(self) -> None:
        """Run through a standard initial configuration routine."""
        self.pitch_down_and_align_camera()
        self.zoom(out=True, minimap=False)
        self.reset_minimap_zoom()
        self.walk_to_random_point_nearby()
        self.close_active_chat_cursor()
        self.toggle_run_on_if_enough_energy()
        if not self.is_chat_tab_open("game"):
            self.open_chat_tab("game")
        if not self.is_control_panel_tab_open("inventory"):
            self.open_control_panel_tab("inventory")
        self.log_msg("Standard initial state configured.")

    # --- Bank---
    def find_and_mouse_to_bank(self, num_retries: int = 10) -> bool:
        """After traveling within range, mouse to the color-tagged bank booth.

        Note that this is a simple wrapper for `find_and_mouse_to_marked_object`.

        Args:
            num_retries (int, optional): The number of times to retry searching if the
                first search failed. Defaults to 10.

        Returns:
            bool: True if we found the bank and moused to it, False otherwise.
        """
        return self.find_and_mouse_to_marked_object(
            color=self.cp.hsv.PURPLE_MARK,
            req_txt=["Bank", "Banker", "Bank booth"],
            req_txt_colors=[
                self.cp.bgr.OFF_WHITE_TEXT,
                self.cp.bgr.OFF_CYAN_TEXT,
                self.cp.bgr.OFF_YELLOW_TEXT,
            ],
            num_retries=num_retries,
        )

    def open_bank_tab(self, tab_num: int) -> None:
        """With the bank open, left-click to switch to the desired stash tab.

        0 is "The Bank of Gielinor", while 1 corresponds to "Tab 1", and so on.

        Args:
            tab_num (int): The integer corresponding to the bank tab.
        """
        path_tab_layout = BOT_IMAGES / "bank" / "bank-tab-layout.png"
        tab_layout = search_img_in_rect(path_tab_layout, self.win.game_view)
        tab_width = 35  # The tab's clickable width with a 1-pixel-wide padding.
        tab_height = 31  # Height before the tab's curve in the top-right corner.
        tab_gap = 5  # Dead space between tabs.
        num_tabs = 10  # Total number of tabs.
        offset = tab_layout.left + 3  # Note the 3-pixel-wide alignment offset.
        bank_tabs = []
        for i in range(num_tabs):
            left = offset + i * (tab_width + tab_gap)
            bank_tabs.append(Rectangle(left, tab_layout.top, tab_width, tab_height))
        tab_name = "The Bank of Gielinor" if tab_num == 0 else f"Tab {tab_num}"
        self.log_msg(f"Opening bank tab: {tab_name}...")
        self.mouse.move_to(bank_tabs[tab_num].random_point())
        self.mouse.click()
        self.log_msg(f"Opened bank tab: {tab_name}", overwrite=True)
        self.sleep()

    def is_bank_window_open(self) -> bool:
        """Return whether or not the bank window is open.

        Returns:
            bool: True if the bank window is open, or False if it isn't.
        """
        bank_frame = self.find_sprite(
            win=self.win.game_view, png="window-open.png", folder="bank"
        )
        if bank_frame:
            return True
        return False

    def bank_left_click_deposit_all(self, mouse_speed="fast"):
        """With the bank window open, mouse to the Deposit All button and left-click it.

        Args:
            mouse_speed (str, optional): The speed to move the mouse. Defaults to
                "fast".
        """
        deposit_all_btn = self.find_sprite(
            win=self.win.game_view, png="deposit-all.png", folder="bank"
        )
        self.mouse.move_to(
            deposit_all_btn.random_point(),
            knotsCount=1,  # Using 0 or 1 here produces more linear movement.
            tween=pytweening.easeOutBack,
            mouseSpeed=mouse_speed,
        )
        self.mouse.click()
        self.log_msg("Inventory dumped via Deposit All button.")
        self.sleep()

    def bank_right_click_deposit_all(
        self,
        png: str,
        folder: str,
        move_cursor: bool = True,
        verbose: bool = False,
    ) -> int:
        """Bank all of an item by right-clicking one and selecting "Deposit-All".

        Args:
            png (str): Name of the PNG filename of the item sprite to be deposited.
            folder (str): Name of the folder under ./src/img/bot containing `png`.
            move_cursor (bool, optional): Whether to move the cursor to an inventory
                slot matching the provided ID before right-clicking. Defaults to True.
            verbose (bool, optional): Whether to include a log message about the item
                ID that was issued the "Deposit-All" command. Defaults to False.

        Returns:
            int: The number of items deposited.
        """
        slots_all = self.get_inv_item_slots(png, folder)
        if move_cursor:
            slots = slots_all[:1]  # Use the first available slot by default.
            deposit_inds = [5, 6, 9, 10, 13, 14]  # These indices are most reliable.
            if any(ind in slots_all for ind in deposit_inds):
                slots = [ind for ind in slots_all if ind in deposit_inds]
            chosen_item_index = random.choice(slots)
            slot = self.win.inventory_slots[chosen_item_index]
            self.mouse.move_to(
                slot.random_point(),
                knotsCount=1,  # Using 0 or 1 here produces more linear movement.
                tween=pytweening.easeOutBack,
                mouseSpeed="fast",
            )
        # With the cursor in position, right-click, then select "Deposit-All".
        if self.right_click_select_context_menu(req_txt="Deposit-All"):
            if verbose:
                self.log_msg(f"Item ID deposited: {png}")
            return len(slots_all)
        return 0

    def open_bank(
        self,
        ctrl_click: bool = False,
        preemptive_loc: Rectangle = None,
        timeout: int = 20,
    ) -> bool:
        """Travel to a bank and open up the bank window.

        Args:
            ctrl_click (bool, optional): Whether to hold control while left-clicking
                the bank. Defaults to False. Defaults to False.
            preemptive_loc (Rectangle, optional): Where to preemptively move the mouse
                while traveling to the bank. Defaults to None (meaning the mouse won't
                move preemptively).
            timeout (int, optional): Number of seconds before defining the attempt to
                open the bank as a success or failure. Defaults to 20.

        Returns:
            bool: True if the bank was opened, False otherwise.
        """
        hold_key = "ctrl" if ctrl_click else None
        self.mouse.click(hold_key=hold_key)  # Click the bank to start moving toward it.
        self.log_msg("Traveling to bank...")
        if preemptive_loc:
            self.mouse.move_to(preemptive_loc.random_point())
        start = time.time()
        window_check = 0
        while not self.is_bank_window_open() and (time.time() - start < timeout):
            window_check += 1
            self.log_msg(f"Traveling to bank...({window_check})", overwrite=True)
            self.sleep()
        return self.is_bank_window_open()

    def close_bank(self) -> None:
        """Close the bank window by pressing Esc (hopefully after depositing)."""
        pag.press("esc")
        self.sleep(0.4, 0.6)
        attempts_to_close = 1
        # Doublecheck that it's closed. Spam escape a few times if necessary.
        while self.is_bank_window_open():
            pag.press("esc", presses=2, interval=rd.biased_trunc_norm_samp(0.1, 0.3))
            self.sleep()
            attempts_to_close += 1
        _s = "" if attempts_to_close == 1 else "s"
        self.log_msg(f"Bank window closed ({attempts_to_close} attempt{_s}).")

    def is_hovering_bank(self) -> bool:
        """Whether the mouse cursor is currently hovering over a bank.

        Returns:
            bool: True if the cursor is hovering over a bank, False otherwise.
        """
        return self.get_mouseover_text(
            contains=["Bank", "Banker", "Bank booth"],
            colors=[
                self.cp.bgr.OFF_WHITE_TEXT,
                self.cp.bgr.OFF_CYAN_TEXT,
                self.cp.bgr.OFF_YELLOW_TEXT,
            ],
        )

    def set_withdraw_qty(
        self,
        qty: int = 14,
        exit_direction: Literal["up", "down", "left", "right"] = "up",
    ) -> None:
        """Set the default Withdraw Quantity to be withdraw via left-click in the bank.

        Args:
            qty (int, optional): The quantity of item to be withdrawn when left-clicked
                in the bank interface. Defaults to 14 (half of an inventory).
            exit_direction (Literal["left", "right"]): If `exit_text` is detected,
                whether to move the cursor up, left, right, or down to dismiss the
                context menu.
        """
        self.log_msg(f"Setting default Withdraw Quantity to {qty}...")
        if (
            btn := self.find_sprite(
                win=self.win.game_view,
                png="quantity-x-clicked.png",
                folder="bank",
                confidence=0.10,
            )
        ) or (
            btn := self.find_sprite(
                win=self.win.game_view,
                png="quantity-x-unclicked.png",
                folder="bank",
                confidence=0.10,
            )
        ):
            self.mouse.move_to(btn.random_point())
        self.mouse.click()
        self.sleep()
        if btn:
            qty_digits = list(str(qty))
            if not self.right_click_select_context_menu(
                req_txt="Set custom quantity",
                exit_txt=f"Default quantity: {qty:,d}",
                exit_direction=exit_direction,
            ):
                msg = f"Default Withdraw Quantity already set to {qty}."
                self.log_msg(msg, overwrite=True)
                return
            self.sleep(1, 2)
            for dig in qty_digits:
                pag.press(dig)
                self.sleep()
            pag.press("enter")
            self.log_msg(f"Default Withdraw quantity set to {qty}.", overwrite=True)
            self.sleep()
        else:
            self.log_msg("Could not find Quantity-X button nor set Withdraw Quantity.")

    def get_price(self, item_id: int) -> int:
        """Get a recent average price of an item at the GE.

        The average price is calulated over a recent approximately 5-minute period.
        Note that the API will return a 400 error if no User-Agent is provided.

        Args:
            item_id (int): The item ID to look up the price for. See `api.item_ids` for
                a large list of available `item_ids`.

        Returns:
            int: The average price of the provided item, or 0 if it's ID was not found.
        """
        url = f"https://prices.runescape.wiki/api/v1/osrs/latest?id={item_id}"
        headers = {"User-Agent": "YourAppName/1.0 (contact@example.com)"}
        response = requests.get(url, headers=headers)
        if response.status_code == 200:  # See: https://httpstatusdogs.com/
            data = response.json()  # Parse the JSON response.
            high_price = data["data"][str(item_id)]["high"]
            low_price = data["data"][str(item_id)]["low"]
            average_price = round((high_price + low_price) / 2)
            return average_price
        return 0

    def get_shorthand_gp_value(self, gp_amt: int) -> str:
        """Return the natural-feeling shorthand value of an amount of gp.

        Args:
            gp_amt (int): An amount of gp (i.e. gold coins).

        Returns:
            str: The shorthand version of the provided gp amount.

        Examples:
            >>> self.__get_shorthand_gp_value(12)
            "12"

            >>> self.__get_shorthand_gp_value(1258)
            "1.26k"

            >>> self.__get_shorthand_gp_value(6100300)
            "6.10M"
        """
        abbreviations = [(1_000, "k"), (1_000_000, "M"), (1_000_000_000, "B")]
        for factor, symbol in abbreviations:
            if abs(gp_amt) >= factor:
                shorthand = gp_amt / factor
                if symbol == "k":
                    return f"{shorthand:.1f}{symbol}"
                return f"{shorthand:.2f}{symbol}"
            if abs(gp_amt) <= abbreviations[0][0]:
                return f"{int(gp_amt):d}"
        return f"{gp_amt:.2f}"

    # --- Player Status ---
    def has_hp_bar(self) -> bool:
        """Return whether the player has an HP bar above their head.

        This is a useful alternative to using OCR to check if the player is in combat.
        This function only works when the game camera is all the way up.

        Returns:
            bool: True if the player has an HP bar above their head, False otherwise.
        """
        # Position our character relative to the screen.
        char_pos = self.win.game_view.center
        # Make a rectangle around our character.
        offset = 30
        char_rect = Rectangle.from_points(
            Point(char_pos.x - offset, char_pos.y - offset),
            Point(char_pos.x + offset, char_pos.y + offset),
        )
        # Take a screenshot of of the rectangle.
        char_screenshot_bgr = char_rect.screenshot()
        char_screenshot_hsv = cv2.cvtColor(char_screenshot_bgr, cv2.COLOR_BGR2HSV)
        # Isolate HP bars in the rectangle.
        hp_bars = isolate_colors(
            char_screenshot_hsv, [self.cp.bgr.RED, self.cp.bgr.GREEN]
        )
        # If there are any HP bars, return True.
        return hp_bars.mean(axis=(0, 1)) != 0.0

    def get_hp(self) -> int:
        """Get our character's HP value.

        Returns:
            int: The HP of the player, or -1 if the value couldn't be read.
        """
        if hp := ocr.scrape_text(
            self.win.hp_orb_text, ocr.PLAIN_11, [self.cp.bgr.GREEN, self.cp.bgr.RED]
        ):
            return int("".join(re.findall(r"\d", hp)))
        return -1

    def get_prayer(self) -> int:
        """Get the Prayer points of the player.

        Returns:
            int: The Prayer point of the player, or -1 if the value couldn't be read.
        """
        if prayer := ocr.scrape_text(
            self.win.prayer_orb_text, ocr.PLAIN_11, [self.cp.bgr.GREEN, self.cp.bgr.RED]
        ):
            return int("".join(re.findall(r"\d", prayer)))
        return -1

    def get_run_energy(self) -> int:
        """Get the run energy of the player.

        Returns:
            int: The run energy the player, or -1 if the value couldn't be read.
        """
        if energy := ocr.scrape_text(
            self.win.run_orb_text,
            ocr.PLAIN_11,
            [
                self.cp.bgr.ORB_TEXT_100_90,
                self.cp.bgr.ORB_TEXT_90_80,
                self.cp.bgr.ORB_TEXT_80_70,
                self.cp.bgr.ORB_TEXT_70_60,
                self.cp.bgr.ORB_TEXT_60_50,
                self.cp.bgr.ORB_TEXT_50_40,
                self.cp.bgr.ORB_TEXT_40_30,
                self.cp.bgr.ORB_TEXT_30_20,
                self.cp.bgr.ORB_TEXT_20_10,
                self.cp.bgr.ORB_TEXT_10_0,
            ],
            exclude_chars=ocr.PROBLEMATIC_CHARS + ["O", "o", "l"],
        ):
            return int("".join(re.findall(r"\d", energy)))
        return -1

    def get_special_energy(self) -> int:
        """Get the special attack energy of the player.

        Returns:
            int: The special energy of the player, or -1 if the value couldn't be read.
        """
        if special_energy := ocr.scrape_text(
            self.win.spec_orb_text,
            ocr.PLAIN_11,
            [self.cp.bgr.ORB_GREEN, self.cp.bgr.ORB_RED],
        ):
            return int("".join(re.findall(r"\d", special_energy)))
        return -1

    def get_total_xp(self) -> int:
        """Get the total XP of the player using OCR.

        Returns:
            int: The total XP of the player, or -1 if the value couldn't be read.
        """
        fonts = [ocr.PLAIN_11, ocr.PLAIN_12, ocr.BOLD_12]
        for font in fonts:
            if xp := ocr.scrape_text(self.win.xp_total, font, self.cp.bgr.WHITE):
                return int("".join(re.findall(r"\d", xp)))
        return -1

    def is_run_on(self) -> bool:
        """Determine whether run is toggled on (yellow) rather than off (brown).

        Returns:
            bool: True if run is toggled on, False otherwise.
        """
        filepath = BOT_IMAGES / "ui_templates" / "orbs" / "run-on.png"
        if search_img_in_rect(filepath, self.win.run_orb, 0.07):
            return True
        return False

    def is_run_off(self) -> bool:
        """Determine whether run is toggled off (brown) rather than on (yellow).

        Returns:
            bool: True if run is toggled off, False otherwise.
        """
        filepath = BOT_IMAGES / "ui_templates" / "orbs" / "run-off.png"
        if search_img_in_rect(filepath, self.win.run_orb, 0.07):
            return True
        return False

    def is_traveling(self, dt: float = 0.603) -> bool:
        """Determine whether our character is traveling at nonzero speed.

        Note that this method is not very precise in terms of immediately recognizing
        if our character has stopped moving. `check_idle_notifier_status` is another
        viable alternative if this method proves insufficient.

        Args:
            dt (float, optional): The time interval in seconds between position
                measurements (required to measure speed). Defaults to 0.603s
                (see `self.game_tick`).

        Returns:
            bool: True if our character is moving across the map, False if they are
                stationary.
        """
        p0 = self.get_world_point()
        time.sleep(dt)
        pf = self.get_world_point()
        return p0 != pf

    def is_not_traveling(self, dt: float = 0.603) -> bool:
        """Determine whether our character is stationary.

        Note that this method acts as a wrapper for `is_traveling`.

        Args:
            dt (float, optional): The time interval in seconds between position
                measurements (required to measure speed). Defaults to 0.63s.

        Returns:
            bool: True if our character is stationary, False if they have nonzero speed.
        """
        return not self.is_traveling(dt=dt)

    def get_world_point(self) -> Tuple[int]:
        """Get our character's world point (i.e. world position) in game tiles.

        This could also be referred to as our characters tile coordinates.

        Returns:
            Tuple[int]: The x-position, y-position, and plane of our character's
                current position in Gielinor, or (-1, -1, -1) if the coordinate could
                not be read.
        """
        x, y, plane = -1, -1, -1
        if text := ocr.scrape_text(
            rect=self.win.tile,
            font=ocr.PLAIN_12,
            colors=self.cp.bgr.WHITE,
            exclude_chars=[char for char in ocr.PROBLEMATIC_CHARS if char != ","],
        ):
            x, y, plane = tuple(map(int, text.replace("Tile", "").split(",")))
        return x, y, plane

    def get_chunk_id(self) -> int:
        """Get our character's global chunk ID (i.e. global 8x8 square-title area).

        Returns:
            int: The unique chunk ID (i.e. 8x8 square-tile zone) our character
                currently resides within, or -1 if the ID could not be read.
        """
        chunk_id = -1
        if text := ocr.scrape_text(
            rect=self.win.chunk_id,
            font=ocr.PLAIN_12,
            colors=self.cp.bgr.WHITE,
        ):
            chunk_id = int(text.replace("ChunkID", ""))
        return chunk_id

    def get_region_id(self) -> int:
        """Get our character's global region ID (i.e. global 64x64 square-title area).

        Returns:
            int: The unique chunk ID (i.e. 64x64 square-tile zone) our character
                currently resides within, or -1 if the ID could not be read.
        """
        region_id = -1
        if text := ocr.scrape_text(
            rect=self.win.region_id,
            font=ocr.PLAIN_12,
            colors=self.cp.bgr.WHITE,
        ):
            region_id = int(text.replace("RegionID", ""))
        return region_id

    # --- Client UI ---
    def _export_all_window_regions(self) -> None:
        """Export all `Window` subregions as BGR-formatted PNG screenshots.

        Note that unlike `window._snapshot_all_window_regions` which captures all the
        bounding boxes on single frame (taking one screenshot), this method switches
        tabs to ensure captured regions represent their associated sprites as intended.
        """
        outfolder_home = (
            BOT_IMAGES.parent / "screen_regions" / self.win.mode / "as_intended"
        )
        if outfolder_home.exists():
            shutil.rmtree(outfolder_home)
        outfolder_home.mkdir(exist_ok=True, parents=True)
        exceptions = [
            "window_title",
            "padding_top",
            "padding_left",
            "mode",
        ]
        allowances = [
            "_minimap_area",
            "_chat_area",
            "_control_panel_area",
            "_game_view_area",
        ]
        for attr_name, attr_value in self.win.__dict__.items():
            if attr_name in allowances or (
                attr_name not in exceptions and not attr_name.startswith("_")
            ):
                outfolder = outfolder_home
                prefix_name_pairs = [
                    ("inv", "inventory"),
                    ("pray", "prayer"),
                    ("spell", "spellbook"),
                ]
                for prefix, name in prefix_name_pairs:
                    if attr_name.startswith(
                        prefix
                    ) and not self.is_control_panel_tab_open(name):
                        self.open_control_panel_tab(name)
                if isinstance(attr_value, list):
                    region_subfolder = outfolder / attr_name
                    region_subfolder.mkdir(exist_ok=True, parents=True)
                    for i, region in enumerate(attr_value):
                        filename = f"{attr_name}_{i}.png"
                        outpath = region_subfolder / filename
                        imsave(outpath, region.screenshot())
                else:
                    filename = f"{attr_name}.png"
                    subfolder = ""
                    if attr_name in ["grid_info", "tile", "chunk_id", "region_id"]:
                        subfolder = "grid_info"
                    elif attr_name.endswith("orb_text"):
                        subfolder = "orb_text"
                    elif attr_name.endswith("orb"):
                        subfolder = "orbs"
                    elif attr_name == "_minimap_area":
                        subfolder = "minimap"
                    elif (
                        attr_name.startswith("cp")
                        or attr_name.endswith("bar")
                        or attr_name == "_control_panel_area"
                    ):
                        subfolder = "control_panel"
                    elif (
                        attr_name in ["chat_input", "chat_tabs_all"]
                        or attr_name == "_chat_area"
                    ):
                        subfolder = "chat"
                    elif attr_name.startswith("inv"):
                        subfolder = "inventory"
                    elif (
                        attr_name
                        not in [
                            "minimap",
                            "chat",
                            "control_panel",
                            "game_view",
                        ]
                        or attr_name == "_game_view_area"
                    ):
                        subfolder = "game_view"
                    outfolder = outfolder / subfolder
                    outfolder.mkdir(exist_ok=True, parents=True)
                    outpath = outfolder / filename
                    imsave(outpath, attr_value.screenshot())
        for folder in outfolder_home.iterdir():
            if folder.is_dir():
                subfolder = ""
                if folder.name.startswith("chat") and folder.name != "chat":
                    subfolder = "chat"
                if folder.name.startswith("cp") and folder.name != "control_panel":
                    subfolder = "control_panel"
                if folder.name.startswith("inv") and folder.name != "inventory":
                    subfolder = "inventory"
                if folder.name == "grid_info":
                    subfolder = "game_view"
                if folder.name in ["orbs", "orb_text"]:
                    subfolder = "minimap"
                containing_folder = outfolder_home / subfolder
                containing_folder.mkdir(exist_ok=True, parents=True)
                new_loc = containing_folder / folder.name
                try:
                    folder.rename(new_loc)
                except FileExistsError:
                    msg = f"{outfolder_home} must not exist or be empty before export."
                    print(msg)
                print(f"Moved {folder.name} into {containing_folder}")

    def logout(self) -> None:
        """Log out of OSRS via the logout icon on the lower control panel.

        Note that the `BotThread` daemon thread continues to run after logout unless it
        is closed with `self.stop`. This function's intended use is for relogging
        without stopping a bot's current run.
        """
        self.log_msg("Logging out...")
        self.mouse.move_to(self.win.cp_tabs[10].random_point())
        self.mouse.click()
        self.sleep()
        self.mouse.move_rel(x=0, y=-53, dx=5, dy=5, mouseSpeed="fastest")
        self.mouse.click()
        self.log_msg("Logged out.", overwrite=True)
        self.sleep()

    def logout_if_greater_than(self, dt: Union[float, int], start: Union[float, int]):
        """Logout if the login time exceeds the specified duration."""
        if time.time() - start > dt and self.num_relogs == 0:
            self.relog()
            self.num_relogs += 1

    def wait_for_img_then_click(
        self,
        png: Union[Path, str],
        folder: Union[Path, str],
    ) -> bool:
        """Wait for an image (i.e. template) to appear, then left-click it.

        Args:
            png (Union[Path, str]): The PNG template that we want to left-click.
            folder (Union[Path, str], optional): The subfolder within src/img/bot
                containing `png`.

        Returns:
            bool: True if the template was left-clicked, False otherwise.
        """
        self.log_msg(f"Attempting to click {png} template...")
        filepath = BOT_IMAGES / folder / png
        start_time = time.time()
        while time.time() - start_time < 60:
            template = search_img_in_rect(filepath, self.win.rectangle())
            if template:
                self.mouse.move_to(template.random_point())
                self.mouse.click()
                self.log_msg(f"Template clicked: {png}", overwrite=True)
                return True
            self.sleep()
        self.log_msg(f"Could not click {png} template.")
        return False

    def login(self) -> None:
        """Log into OSRS from the home splash."""
        self.log_msg("Logging in...")  # Click [Play Now] on the home splash.
        if not self.wait_for_img_then_click("play-now.png", folder="login"):
            self.wait_for_img_then_click("play-now-gray.png", folder="login")
        self.take_break(lo=9, hi=11)  # The client takes a few seconds to connect.
        self.wait_for_img_then_click("click-here-to-play.png", folder="login")
        self.sleep()
        self.log_msg("Moving the mouse around as we get started...")
        # Move the mouse around to simulate getting situated to start playing.
        for _ in range(random.randint(3, 7)):
            self.move_mouse_randomly()
        self.log_msg("Mouse is in position. Ready to play.", overwrite=True)
        self.log_msg("Logged in.")

    def relog(self) -> None:
        """Log out of OSRS and then log back in again.

        This function is designed to get around the forced logout at 6 hours.
        """
        self.log_msg("Relogging...")
        self.logout()
        self.login()
        self.log_msg("Relog successful.", overwrite=True)

    def logout_and_stop_script(self, msg: str) -> None:
        """Log out of the RuneLite client and stop the script.

        Args:
            msg (str): The final message to be sent before logging out.
        """
        self.log_msg(msg)
        self.logout()
        self.stop()
