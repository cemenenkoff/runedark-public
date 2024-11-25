import time
from typing import Dict, List, Literal, Tuple

import pyautogui as pag
import pygetwindow as gw
import win32con
import win32gui
from matplotlib.pyplot import imsave

import utilities.img_search as imsearch
from model.window import Window, WindowInitializationError
from utilities.geometry import Rectangle
from utilities.mappings import subtract_windows as sw


class RuneLiteWindow(Window):
    """`RuneLiteWindow` lets us interact with the RuneLite active window.

    `RuneLiteWindow` is an extension of the `Window` class, designed specifically
    for locating and interacting with key UI elements on the RuneLite screen. It
    provides methods and attributes tailored for RuneLite client automation.

    `RuneLiteWindow` splits a RuneLite client window into several sub-regions.

    To get a better idea about each sub-region, see src/img/explanatory or call
    `_export_all_region_screenshots` and then see src/img/screen_regions. Note that the
    general order of how windows are contructed follows
        minimap > chat > control panel > game view
    Many sub-methods rely on geometry defined earlier up in the chain, so maintaining
    this general ordering is key when adding new regions.
    """

    # Minimap
    compass_orb: Rectangle = None
    hp_orb_text: Rectangle = None
    minimap: Rectangle = None
    _minimap_area: Rectangle = None
    prayer_orb_text: Rectangle = None
    prayer_orb: Rectangle = None
    run_orb_text: Rectangle = None
    run_orb: Rectangle = None
    spec_orb_text: Rectangle = None
    spec_orb: Rectangle = None
    mode: str = ""

    # Chatbox
    chat: Rectangle = None
    _chat_area: Rectangle = None
    chat_tabs: List[Rectangle] = []
    chat_tabs_all: Rectangle = None
    chat_input: Rectangle = None
    chat_history: List[Rectangle] = []

    # Control Panel
    control_panel: Rectangle = None
    _control_panel_area: Rectangle = None
    hp_bar: Rectangle = None
    prayer_bar: Rectangle = None
    cp_tabs: List[Rectangle] = []
    cp_top: Rectangle = None
    cp_bot: Rectangle = None
    cp_inner: Rectangle = None
    inventory: Rectangle = None
    inventory_slots: List[Rectangle] = []
    spellbook_normal: List[Rectangle] = []
    prayers: List[Rectangle] = []

    # Game View
    game_view: Rectangle = None
    _game_view_area: Rectangle = None
    mouseover: Rectangle = None
    xp_total: Rectangle = None
    grid_info: Rectangle = None
    tile: Rectangle = None
    chunk_id: Rectangle = None
    region_id: Rectangle = None
    current_action: Rectangle = None

    def __init__(self, window_title: str) -> None:
        """Initialize a `RuneLiteWindow`.

        Args:
            window_title (str): The title of the application window to interact with.
        """
        super().__init__(window_title, padding_top=26, padding_left=19)

    def is_runelite_active_window(self) -> bool:
        """Check if the RuneLite window is the currently active window.

        Returns:
            bool: True if RuneLite is the focused, active window, False otherwise.
        """
        current_window = pag.getActiveWindow()
        if current_window is not None:
            return "runelite" in current_window.title.lower()

    def switch_window_to_runelite(self) -> bool:
        """Switch the active window to RuneLite.

        Returns:
            bool: True if the active window was switched to RuneLite, False otherwise.
        """
        runelite_windows = [win for win in gw.getAllTitles() if "RuneLite" in win]
        if not runelite_windows:
            print("RuneLite window not found.")
            return False
        runelite_window = gw.getWindowsWithTitle(runelite_windows[0])[0]
        hwnd = runelite_window._hWnd  # Get the window handle.
        # Use win32gui to bring the window to the front
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)  # Restore if minimized.
        win32gui.SetForegroundWindow(hwnd)  # Bring the window to the foreground.
        time.sleep(0.5)  # Wait briefly to allow window switching to complete.
        print(f"Switched to window: {runelite_window.title}.")
        return True

    def resize(self, width: int = 773, height: int = 534) -> None:
        """Resize the client window.

        This method is intended to verride `Window.resize` to resize precisely to the
        classic OSRS window size.

        By default, the OSRS client window has dimensions of 773x534 in Fixed - Classic
        layout. Switching RuneLite to this layout can be done under Display Settings
        tab in the settings menu (i.e. wrench icon menu).

        Args:
            width (int): The width to resize the window to. Defaults to 773.
            height (int): The height to resize the window to. Defaults to 534.
        """
        if client := self.window:  # Make sure the window exists before resizing.
            client.size = (width, height)

    def initialize(self) -> bool:
        """Initialize the client window by locating critical UI regions.

        This method should be called when a bot is started or resumed (done by default).

        Raises:
            WindowInitializationError: Raised if the client failed to initialize all
                critical aspects of the RuneLite UI.

        Returns:
            bool: True if successful, else a `WindowInitializationError` is raised.
        """
        start_time = time.time()
        self.switch_window_to_runelite()
        client_rect = self.rectangle()
        try:
            a = self._locate_minimap(client_rect)
            b = self._locate_chat(client_rect)
            c = self._locate_control_panel(client_rect)
            d = self._locate_game_view()
            if all([a, b, c, d]):
                print(f"Window.initialize() took {time.time() - start_time} seconds.")
                return True
            return False
        except Exception:
            raise WindowInitializationError()

    def _gen_subtract_boxes(
        self,
        region_name: str,
        widths: Tuple[int],
        style: Literal["left", "right"],
    ) -> List[Dict]:
        """Generate a list of rectangles to be subtracted from the region's left side.

        These generated rectangles are used to blacken out areas of the region. The
        rectangles stack on the region's left side from top to bottom.

        Args:
            region_name (str): The name of the associated window subregion attribute.
                Examples include "game_view" or "minimap".
            widths (Tuple[int]): The widths of 1-pixel-tall rectangles to subtract, all
                of them with t left-coordinate at the left edge of the provided region.
            style (Literal["left", "right"]): Whether the `Rectangle` objects to
                subtract will be measured from the left of the region or the right of
                the region.

        Returns:
            List[Dict]: A list of rectangles to subtract from the region provided.
        """
        region = getattr(self, region_name)
        subtract_boxes = []
        for i in range(region.height):
            if style == "left":
                left, width = 0, widths[i]
            if style == "right":
                left, width = widths[i], region.width - widths[i]
            if width != 0:  # Pixel rows that shouldn't include blacking have width 0.
                subtract_boxes.append(
                    {
                        "left": left,
                        "top": i,
                        "height": 1,
                        "width": width,
                    }
                )
        return subtract_boxes

    def _locate_minimap(self, client_rect: Rectangle) -> bool:
        """Locate the minimap area on the clent window.

        This involves not only finding the bounding `Rectangle` for the minimap, but
        also the bounding `Rectangle` objects for all of its internal positions.

        Args:
            client_rect (Rectangle): The client area to search within.

        Returns:
            bool: True if the minimap and its contents were located successfully, False
                otherwise.
        """
        # Note that `mt` refers to the minimap template.
        # Fixed - Classic layout minimap UI.
        if mt := imsearch.search_img_in_rect(
            imsearch.BOT_IMAGES / "ui_templates" / "minimap-fixed-classic.png",
            client_rect,
        ):
            self._minimap_area = (
                Rectangle(  # For the `game_view` subtraction rectangle.
                    left=mt.left - 1,
                    top=mt.top,
                    width=mt.width + 48,
                    height=mt.height + 4,
                )
            )
            self.compass_orb = Rectangle(
                left=mt.left + 27, top=mt.top + 2, width=34, height=35
            )
            self.compass_orb.subtract_list = self._gen_subtract_boxes(
                region_name="compass_orb",
                widths=sw.FIXED_COMPASS_LEFT_WIDTHS,
                style="left",
            ) + self._gen_subtract_boxes(
                region_name="compass_orb",
                widths=sw.FIXED_COMPASS_RIGHT_WIDTHS,
                style="right",
            )
            self.hp_orb = Rectangle(
                left=mt.left + 25, top=mt.top + 43, width=28, height=28
            )
            self.hp_orb.subtract_list = self._gen_subtract_boxes(
                region_name="hp_orb", widths=sw.FIXED_ORB_LEFT_WIDTHS, style="left"
            ) + self._gen_subtract_boxes(
                region_name="hp_orb", widths=sw.FIXED_ORB_RIGHT_WIDTHS, style="right"
            )
            self.prayer_orb = Rectangle(
                left=mt.left + 25, top=mt.top + 77, width=28, height=28
            )
            self.prayer_orb.subtract_list = self._gen_subtract_boxes(
                region_name="prayer_orb", widths=sw.FIXED_ORB_LEFT_WIDTHS, style="left"
            ) + self._gen_subtract_boxes(
                region_name="prayer_orb",
                widths=sw.FIXED_ORB_RIGHT_WIDTHS,
                style="right",
            )
            self.run_orb = Rectangle(
                left=mt.left + 35, top=mt.top + 109, width=28, height=28
            )
            self.run_orb.subtract_list = self._gen_subtract_boxes(
                region_name="run_orb", widths=sw.FIXED_ORB_LEFT_WIDTHS, style="left"
            ) + self._gen_subtract_boxes(
                region_name="run_orb", widths=sw.FIXED_ORB_RIGHT_WIDTHS, style="right"
            )
            self.spec_orb = Rectangle(
                left=mt.left + 57, top=mt.top + 134, width=28, height=28
            )
            self.spec_orb.subtract_list = self._gen_subtract_boxes(
                region_name="spec_orb", widths=sw.FIXED_ORB_LEFT_WIDTHS, style="left"
            ) + self._gen_subtract_boxes(
                region_name="spec_orb", widths=sw.FIXED_ORB_RIGHT_WIDTHS, style="right"
            )
            self.hp_orb_text = Rectangle(
                left=mt.left + 3, top=mt.top + 54, width=22, height=14
            )
            self.prayer_orb_text = Rectangle(
                left=mt.left + 3, top=mt.top + 88, width=22, height=14
            )
            self.run_orb_text = Rectangle(
                left=mt.left + 13, top=mt.top + 120, width=22, height=14
            )
            self.spec_orb_text = Rectangle(
                left=mt.left + 35, top=mt.top + 145, width=22, height=14
            )
            self.minimap = Rectangle(
                left=mt.left + 52, top=mt.top + 4, width=147, height=159
            )
            # Take a series of 1-pixel bites out of the minimap to crop it perfectly.
            self.minimap.subtract_list = self._gen_subtract_boxes(
                region_name="minimap", widths=sw.FIXED_MINIMAP_LEFT_WIDTHS, style="left"
            ) + self._gen_subtract_boxes(
                region_name="minimap",
                widths=sw.FIXED_MINIMAP_RIGHT_WIDTHS,
                style="right",
            )
            self.mode = "fixed_classic"
            return True

        # Resizable - Classic layout minimap UI.
        if mt := imsearch.search_img_in_rect(
            imsearch.BOT_IMAGES / "ui_templates" / "minimap-resizable-classic.png",
            client_rect,
        ):
            self._minimap_area = (
                Rectangle(  # For the `game_view` subtraction rectangle.
                    left=mt.left, top=mt.top, width=mt.width + 1, height=mt.height + 15
                )
            )
            self.compass_orb = Rectangle(
                left=mt.left + 33, top=mt.top + 2, width=37, height=37
            )
            self.compass_orb.subtract_list = self._gen_subtract_boxes(
                region_name="compass_orb",
                widths=sw.RESIZABLE_COMPASS_LEFT_WIDTHS,
                style="left",
            ) + self._gen_subtract_boxes(
                region_name="compass_orb",
                widths=sw.RESIZABLE_COMPASS_RIGHT_WIDTHS,
                style="right",
            )
            self.hp_orb = Rectangle(
                left=mt.left + 26, top=mt.top + 48, width=28, height=28
            )
            self.hp_orb.subtract_list = self._gen_subtract_boxes(
                region_name="hp_orb", widths=sw.RESIZABLE_ORB_LEFT_WIDTHS, style="left"
            ) + self._gen_subtract_boxes(
                region_name="hp_orb",
                widths=sw.RESIZABLE_ORB_RIGHT_WIDTHS,
                style="right",
            )
            self.prayer_orb = Rectangle(
                left=mt.left + 26, top=mt.top + 82, width=28, height=28
            )
            self.prayer_orb.subtract_list = self._gen_subtract_boxes(
                region_name="prayer_orb",
                widths=sw.RESIZABLE_ORB_LEFT_WIDTHS,
                style="left",
            ) + self._gen_subtract_boxes(
                region_name="prayer_orb",
                widths=sw.RESIZABLE_ORB_RIGHT_WIDTHS,
                style="right",
            )
            self.run_orb = Rectangle(
                left=mt.left + 36, top=mt.top + 114, width=28, height=28
            )
            self.run_orb.subtract_list = self._gen_subtract_boxes(
                region_name="run_orb", widths=sw.RESIZABLE_ORB_LEFT_WIDTHS, style="left"
            ) + self._gen_subtract_boxes(
                region_name="run_orb",
                widths=sw.RESIZABLE_ORB_RIGHT_WIDTHS,
                style="right",
            )
            self.spec_orb = Rectangle(
                left=mt.left + 58, top=mt.top + 139, width=28, height=28
            )
            self.spec_orb.subtract_list = self._gen_subtract_boxes(
                region_name="spec_orb",
                widths=sw.RESIZABLE_ORB_LEFT_WIDTHS,
                style="left",
            ) + self._gen_subtract_boxes(
                region_name="spec_orb",
                widths=sw.RESIZABLE_ORB_RIGHT_WIDTHS,
                style="right",
            )
            self.hp_orb_text = Rectangle(
                left=mt.left + 4, top=mt.top + 59, width=22, height=14
            )
            self.prayer_orb_text = Rectangle(
                left=mt.left + 4, top=mt.top + 93, width=22, height=14
            )
            self.run_orb_text = Rectangle(
                left=mt.left + 14, top=mt.top + 125, width=22, height=14
            )
            self.spec_orb_text = Rectangle(
                left=mt.left + 36, top=mt.top + 150, width=22, height=14
            )
            self.minimap = Rectangle(
                left=mt.left + 48, top=mt.top + 1, width=162, height=162
            )
            # Take a series of 1-pixel bites out of the minimap to crop it perfectly.
            self.minimap.subtract_list = self._gen_subtract_boxes(
                region_name="minimap",
                widths=sw.RESIZABLE_MINIMAP_LEFT_WIDTHS,
                style="left",
            ) + self._gen_subtract_boxes(
                region_name="minimap",
                widths=sw.RESIZABLE_MINIMAP_RIGHT_WIDTHS,
                style="right",
            )
            self.mode = "resizable_classic"
            return True
        print("Failed to find minimap.")
        return False

    def _locate_chat(self, client_rect: Rectangle) -> bool:
        """Locate the chatbox area (and sub-areas) on the client bounding `Rectangle`.

        In addition to the chat box, lists of `Rectangle` objects isolating chat tabs,
        chat history, and chat input are all defined here. Note importantly that the
        game view is calculated relative to the chat box.

        Args:
            client_rect (Rectangle): The client area to search within.

        Returns:
            bool: True if the chatbox was found, False otherwise.
        """
        num_tabs = 7  # Exclude the Report button in this count.
        num_lines = 8  # There are 8 lines of chat history.
        btn_height = 22
        btn_width = 56
        btn_spacing = 6
        scrollbar_width = 16
        scrollbar_x_offset = 1
        border_thickness = 6
        border_thickness_input_hist_separator = 1
        input_line_y_offset = 1
        line_height_ocr_bot_pad = 2
        line_height = 14
        x0 = 2  # Initial x-offset.
        if chat := imsearch.search_img_in_rect(
            imsearch.BOT_IMAGES.joinpath("ui_templates", "chat.png"), client_rect
        ):
            self.chat_tabs = []
            for i in range(num_tabs):
                self.chat_tabs.append(
                    Rectangle(
                        left=chat.left + x0 + i * (btn_width + btn_spacing),
                        top=chat.top + chat.height - btn_height,
                        width=btn_width,
                        height=btn_height,
                    )
                )
            self.chat_tabs_all = Rectangle(
                left=chat.left,
                top=chat.top + chat.height - btn_height - border_thickness // 2,
                width=chat.width,
                height=btn_height + border_thickness,
            )
            self.chat_history = []
            # Set the y-offset to skip the current "Press Enter to Chat..." line. This
            # offset is purposefully built up from the bottom of the chat window.
            y0 = (
                btn_height
                + border_thickness
                + input_line_y_offset
                + line_height
                + border_thickness_input_hist_separator
                + line_height
            )
            for i in range(num_lines):
                self.chat_history.append(
                    Rectangle(
                        left=chat.left + border_thickness,
                        top=chat.top + chat.height - y0 - (i * line_height) - 1,
                        width=chat.width
                        - 2 * border_thickness
                        - scrollbar_width
                        - scrollbar_x_offset,
                        height=line_height + line_height_ocr_bot_pad,
                    )
                )
            # Note the seemingly arbitrary pad of -1 for `top` and +2 for `height`.
            # This is due to the fact that all tokens in `ocr.PLAIN_12` are 16 pixels
            # tall because they include a 1-pixel tall padding above and below.
            self.chat_input = Rectangle(
                left=chat.left + border_thickness,
                top=chat.top
                + chat.height
                - y0
                + line_height
                + border_thickness_input_hist_separator,
                width=chat.width - 2 * border_thickness,
                height=line_height + line_height_ocr_bot_pad,
            )
            self.chat = chat
            self._chat_area = Rectangle(  # Used for blackening out the game view.
                chat.left,
                chat.top - 1,
                width=chat.width,
                height=chat.height + line_height_ocr_bot_pad,
            )
            return True
        print("Failed to find chatbox.")
        return False

    def _locate_control_panel(self, client_rect: Rectangle) -> bool:
        """Locate the control panel area on the client bounding `Rectangle`.

        Args:
            client_rect (Rectangle): The client area to search within.

        Returns:
            bool: True if the control panel was found, False otherwise.
        """
        if cp := imsearch.search_img_in_rect(
            imsearch.BOT_IMAGES / "ui_templates" / "control-panel.png", client_rect
        ):
            # Adjusted because the raw template doesn't include a the top border.
            self.control_panel = Rectangle(
                left=cp.left, top=cp.top - 1, width=cp.width, height=cp.height + 1
            )
            # The `control_panel_area` is for blackening out the game view.
            self._control_panel_area = self.control_panel
            if self.mode == "fixed_classic":
                self._control_panel_area = Rectangle(
                    left=self._minimap_area.left,
                    top=self.control_panel.top,
                    width=self._minimap_area.width,
                    height=cp.height + 1,
                )
            self._locate_hp_prayer_bars()
            self._locate_cp_tabs()
            self._locate_inv_slots()
            self._locate_prayers()
            self._locate_spells()
            return True
        print("Failed to find control panel.")
        return False

    def _locate_hp_prayer_bars(self) -> None:
        """Create `Rectangle` objects for the HP and Prayer bars.

        The HP and Prayer bars on either side of the control panel orient the main
        RuneLite UI `Rectangle` objects, so they are stored as class properties.
        """
        bar_w, bar_h = 18, 250  # Dimensions of the bars.
        self.hp_bar = Rectangle(
            left=self.control_panel.left + 6,
            top=self.control_panel.top + 42,
            width=bar_w,
            height=bar_h,
        )
        self.prayer_bar = Rectangle(
            left=self.hp_bar.top_right.x + 192,
            top=self.hp_bar.top,
            width=bar_w,
            height=bar_h,
        )

    def _locate_cp_tabs(self) -> None:
        """Create and store `Rectangle` objects for each interface tab.

        For each tab (i.e. inventory, prayer, etc.), generate a `Rectangle` relative to
        the control panel and append it to the `self.cp_tabs` class property.
        """
        slot_w_outer, slot_w_inner = 33 + 4, 33
        slot_h = 36
        gap_inv = 263
        self.cp_top = Rectangle(
            left=self.control_panel.left,
            top=self.control_panel.top,
            width=2 * slot_w_outer + 5 * slot_w_inner,
            height=slot_h,
        )
        self.cp_bot = Rectangle(
            left=self.control_panel.left,
            top=self.control_panel.top + slot_h + gap_inv - 1,
            width=241,
            height=slot_h + 1,  # The bottom tabs are slightly taller than the top ones.
        )
        self.cp_tabs = []
        for i in range(2):
            x = self.control_panel.left + 1
            y = self.control_panel.top if i == 0 else self.cp_bot.top
            for j in range(7):
                slot_w = slot_w_outer if j in [0, 7] else slot_w_inner
                tab = Rectangle(
                    left=x,
                    top=y,
                    width=slot_w,
                    height=slot_h,
                )
                self.cp_tabs.append(tab)
                x += slot_w

    def _locate_inv_slots(self) -> None:
        """Create and store `Rectangle` objects for each inventory slot.

        For each inventory slot, generate a `Rectangle` relative to the control panel
        and append it to the `self.inventory_slots` class property. Each `Rectangle`
        represents an inventory slot, ordered by going across rows left-to-right, then
        down.

        Args:
            cp (Rectangle): `Rectangle` object acting as a bounding box for the control
                panel area of the RuneLite client UI.
        """
        self.inventory = Rectangle(
            left=self.control_panel.left + 27,
            top=self.control_panel.top + 41,
            width=186,
            height=251,
        )
        self.cp_inner = self.inventory  # The inner control panel pane is the same area.
        self.inventory_slots = []  # There are 7 rows and 4 columns to make 28 slots.
        # Note that the slots overlap slightly to provide more border padding.
        # 1 [38x45] [38x45] [38x45] [38x45] 1
        # 1    1       1       1       1    1
        # 1 [38x45] [38x45] [38x45] [38x45] 1
        # 1    1       1       1       1    1
        # 1   ...     ...     ...     ...   1
        # 1    1       1       1       1    1
        # 1 [38x45] [38x45] [38x45] [38x45] 1
        slot_h, slot_w = 36, 42  # Dimensions of the bounding box for each inv slot.
        # These offsets are calibrated to give generally centered, symmetrical bounding
        # boxes for item slots with an adequate border to enable better template
        # matching. They define the extent to which the slots overlap.
        x = self.inventory.left + 9
        y = self.inventory.top + 1
        for j in range(7):
            for i in range(4):
                slot = Rectangle(
                    left=x + i * slot_w - 1,
                    top=y + j * slot_h - 1,
                    width=slot_w + 1,
                    height=slot_h + 3,
                )
                self.inventory_slots.append(slot)

    def _locate_prayers(self) -> None:
        """Create and store `Rectangle` objects for each prayer in the prayer menu.

        For each prayer in the prayer menu, generate a `Rectangle` relative to the
        control panel and append it to the `self.prayers` class property.

        Args:
            cp (Rectangle): `Rectangle` object acting as a bounding box for the control
                panel area of the RuneLite client UI.
        """
        self.prayers = []
        slot_w, slot_h = 37, 37  # Dimensions of the bounding box for each prayer.
        y = self.cp_inner.top + 3
        for _ in range(6):
            x = self.cp_inner.left
            for _ in range(5):
                self.prayers.append(
                    Rectangle(left=x, top=y, width=slot_w, height=slot_h)
                )
                x += slot_w
            y += slot_h
        del self.prayers[29]  # Remove the last slot since it is unused in-game.

    def _locate_spells(self) -> None:
        """Create and store `Rectangle` objects for each spell in the spell menu.

        For each spell in the spell menu, generate a `Rectangle` relative to the
        control panel and append it to the `self.spellbook_normal` class property.

        Note that this method currently only accomodates normal spellbook spells.

        Args:
            cp (Rectangle): `Rectangle` object acting as a bounding box for the control
                panel area of the RuneLite client UI.
        """
        self.spellbook_normal = []
        slot_w, slot_h = 26, 24  # Dimensions of the bounding box for each spell.
        y = self.control_panel.top + 36
        for _ in range(10):
            x = self.control_panel.left + 29
            for _ in range(7):
                self.spellbook_normal.append(
                    Rectangle(left=x, top=y, width=slot_w, height=slot_h)
                )
                x += slot_w
            y += slot_h
        self.spellbook_normal = self.spellbook_normal[:-5]  # Remove blank areas.

    def _locate_game_view(self) -> bool:
        """Locate the game view while the client is in Fixed - Classic layout mode.

        Returns:
            bool: True if the game view was located successfully, False otherwise.
        """
        if not all([self.minimap, self.chat, self.control_panel]):
            print("Failed to find game view. Missing critical regions.")
            return False
        self._game_view_area = Rectangle(
            left=self.chat.left,
            top=self._minimap_area.top,
            width=self.control_panel.bottom_right.x - self.chat.left,
            height=self.control_panel.bottom_right.y - self._minimap_area.top,
        )
        subtract_regions = [
            self._minimap_area,
            self._chat_area,
            self._control_panel_area,
        ]
        self._game_view_area.subtract_list = [
            {
                "left": region.left - self._game_view_area.left,
                "top": region.top - self._game_view_area.top,
                "height": region.height,
                "width": region.width,
            }
            for region in subtract_regions
        ]
        self.game_view = self._game_view_area
        if self.mode == "fixed_classic":
            pad = 3  # There is a hardcoded pad of 3 on the top and left in this mode.
            # Make the pad match on the bot and right sides to form a 3-pixel border.
            # Having a bit of padding aids in overall template matching with OpenCV.
            self.game_view = Rectangle(
                left=self._game_view_area.left,
                top=self._game_view_area.top,
                width=self._game_view_area.width - self._minimap_area.width + pad,
                height=self._game_view_area.height - self._chat_area.height + pad,
            )

        self._locate_mouseover()
        self._locate_grid_info()
        self._locate_current_action()
        self._locate_xp_total()
        return True

    def _locate_mouseover(self):
        """Create and store a `Rectangle` for the game view's mouseover text area."""
        self.mouseover = Rectangle(
            left=self.game_view.left, top=self.game_view.top, width=407, height=26
        )

    def _locate_grid_info(self) -> None:
        """Locate the Grid Info box from the World Location RuneLite plug-in.

        Note that this requires the World Location RuneLite plug-in to be installed
        with Grid Info [✔], causing an information box to appear in the upper-left
        corner of the game view.
        """
        char_height = 16  # Defined by the pixel height of the tokens in `ocr.PLAIN_12`!
        dy = 1
        grid_info_width = 125  # This gives 2 pixels of left-right padding.
        left_offset = 10 if self.mode == "fixed_classic" else 6
        top_offset = 28 if self.mode == "fixed_classic" else 23
        self.grid_info = Rectangle(
            left=self.game_view.left + left_offset,
            top=self.game_view.top + top_offset,
            width=grid_info_width,
            height=3 * (char_height + dy),
        )
        self.tile = Rectangle(
            left=self.grid_info.left,
            top=self.grid_info.top,
            width=grid_info_width,
            height=char_height + dy,
        )
        self.chunk_id = Rectangle(
            left=self.tile.left,
            top=self.tile.top + self.tile.height - dy,
            width=grid_info_width,
            height=char_height + dy,
        )
        self.region_id = Rectangle(
            left=self.chunk_id.left,
            top=self.chunk_id.top + self.chunk_id.height - dy,
            width=grid_info_width,
            height=char_height + 3 * dy,  # Extend dy here to for the g in Region.
        )

    def _locate_current_action(self) -> None:
        """Create and store a `Rectangle` for the game view's current action area.

        Note that when a current action window is active (e.g. in combat), the grid
        info box is shifted downward. This shouldn't be an issue unless our character
        is attempting to use a `Walker` while simultaneously reading from the current
        action view. If this needs to be adjusted, holding Alt within RuneLite to then
        drag and drop the grid info region to another location will rectify the
        situation, though `_locate_grid_info` will need to be adjusted accordingly.
        """
        self.current_action = Rectangle(
            left=self.game_view.left + 10,
            top=self.game_view.top + 24,
            width=125,
            height=18,
        )

    def _locate_xp_total(self) -> None:
        """Create and store a `Rectangle` for the game view's total XP area."""
        left_offset = 105 if self.mode == "fixed_classic" else 144
        top_offset = 9 if self.mode == "fixed_classic" else 4
        self.xp_total = Rectangle(
            left=self._minimap_area.left - left_offset,
            top=self.game_view.top + top_offset,
            width=101,
            height=16,
        )

    def _snapshot_all_window_regions(self) -> None:
        """Snapshot of all `Window` subregions as BGR-formatted PNG screenshots."""
        outfolder = (
            imsearch.BOT_IMAGES.parent / "screen_regions" / self.mode / "snapshot"
        )
        outfolder.mkdir(exist_ok=True, parents=True)

        for attr_name, attr_value in self.__dict__.items():
            exceptions = ["window_title", "padding_top", "padding_left", "mode"]
            if not attr_name.startswith("_") and attr_name not in exceptions:
                if isinstance(attr_value, list):
                    region_subfolder = outfolder / attr_name
                    region_subfolder.mkdir(exist_ok=True, parents=True)
                    for i, region in enumerate(attr_value):
                        filename = f"{attr_name}_{i}.png"
                        outpath = region_subfolder / filename
                        imsave(outpath, region.screenshot())
                else:
                    filename = f"{attr_name}.png"
                    outpath = outfolder / filename
                    imsave(outpath, attr_value.screenshot())
