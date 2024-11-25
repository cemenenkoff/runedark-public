import time

from model.osrs.osrs_bot import OSRSBot

# from utilities.api.gi_tracker import GITracker


class OSRSUtilTester(OSRSBot):
    def __init__(self):
        super().__init__(bot_title="Util Tester", description="Test utility functions.")
        self.run_time = 600
        self.options_set = True

    def create_options(self):
        """Add bot options.

        Use an `OptionsBuilder` to define the options for the bot. For each function
        call below, we define the type of option we want to create, its key, a label
        for the option that the user will see, and the possible values the user can
        select. The key is used in the `save_options` method to unpack the dictionary
        of options after the user has selected them.
        """
        self.options_builder.add_slider_option(
            "run_time", "How long to run (minutes)?", 1, 600
        )

    def save_options(self, options: dict):
        """Load options into the bot object.

        For each option in the dictionary, if it is an expected option, save the value
        as a property of the bot. If any unexpected options are found, log a warning.
        If an option is missing, set the `options_set` flag to False.
        """
        for option in options:
            if option == "run_time":
                self.run_time = options[option]
            else:
                self.log_msg(f"Unknown option: {option}")
                print("Options are packed incorrectly.")
                self.options_set = False
                return
        self.log_msg(f"Running time: {self.run_time} minutes.")
        self.log_msg("Options set successfully.")
        self.options_set = True

    def main_loop(self):
        # api_g = GITracker(verbose=False)
        run_time_str = f"{self.run_time // 60}h {self.run_time % 60}m"  # e.g. 6h 0m
        self.log_msg(f"[START] ({run_time_str})", overwrite=True)
        start_time = time.time()
        end_time = int(self.run_time) * 60  # Measured in seconds.
        # self.relog()
        # self._export_all_window_regions()
        # self.win._snapshot_all_window_regions()
        # self._export_compass_map()
        while time.time() - start_time < end_time:
            msg = (
                # f"Is our inventory full? {self.is_inventory_full()}\n"
                # f"Is our inventory empty? {self.is_inventory_empty()}\n"
                # f"Number of empty inventory slots: {self.get_num_empty_inv_slots()}\n"
                # f"Is slot 28 full? {self.is_inv_slot_28_full()}\n"
                # f"Is there at least one empty slot? {self.inv_has_space()}\n"
                f"OCR-based world point: {self.get_world_point()}\n"
                f"OCR-based chunk ID: {self.get_region_id()}\n"
                f"OCR-based chunk ID: {self.get_chunk_id()}\n"
                f"compass angle: {self.get_compass_angle()}\n"
                # f"username: {api_g.get_quests_summary()}\n"
                # f"Combat open? {self.is_control_panel_tab_open('combat_options')}\n"
                # f"Skills open? {self.is_control_panel_tab_open('skills')}\n"
                # f"Char open? {self.is_control_panel_tab_open('character_summary')}\n"
                # f"Inv open? {self.is_control_panel_tab_open('inventory')}\n"
                # f"Equip open? {self.is_control_panel_tab_open('worn_equipment')}\n"
                # f"Prayer open? {self.is_control_panel_tab_open('prayer')}\n"
                # f"Spells open? {self.is_control_panel_tab_open('spellbook')}\n"
                # f"Chat open? {self.is_control_panel_tab_open('chat_channel')}\n"
                # f"Friends open? {self.is_control_panel_tab_open('friends_list')}\n"
                # f"Acc open? {self.is_control_panel_tab_open('account_management')}\n"
                # f"Logout open? {self.is_control_panel_tab_open('logout')}\n"
                # f"Settings open? {self.is_control_panel_tab_open('settings')}\n"
                # f"Emotes open? {self.is_control_panel_tab_open('emotes')}\n"
                # f"Music open? {self.is_control_panel_tab_open('music_player')}\n"
                # f"All open? {self.is_chat_tab_open('all')}\n"
                # f"Game open? {self.is_chat_tab_open('game')}\n"
                # f"Public open? {self.is_chat_tab_open('public')}\n"
                # f"Private open? {self.is_chat_tab_open('private')}\n"
                # f"Channel open? {self.is_chat_tab_open('channel')}\n"
                # f"Clan open? {self.is_chat_tab_open('clan')}\n"
                # f"Trade open? {self.is_chat_tab_open('trade')}\n"
                # "-----------------------------\n"
                f"idle notifier text (stopped_moving)?"
                f" {self.check_idle_notifier_status('stopped_moving')}\n"
                # "-----------------------------\n"
                f"Price gold bars? {self.get_price(2357)} gp\n"
                f"Price silver bars? {self.get_price(2355)} gp\n"
                f"Price emeralds? {self.get_price(1605)} gp\n"
                f"Price emerald bracelets? {self.get_price(11076)} gp\n"
            )
            self.move_mouse_randomly()
            self.sleep(1, 2)
            self.move_mouse_randomly()
            self.sleep(1, 2)
            self.move_mouse_randomly()
            self.sleep(1, 2)
            self.move_mouse_randomly()
            # self.win._export_all_regions()
            self.log_msg(msg)
            # time.sleep(0.1)
        self.update_progress((time.time() - start_time) / end_time)
        self.update_progress(1)
        self.log_msg("[END]")
        self.stop()
