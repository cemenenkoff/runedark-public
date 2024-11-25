import json
import logging
import math
import sys
import threading
import time
from datetime import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Literal, Tuple, Union

from flask import Flask, jsonify, request

if __name__ == "__main__":
    import sys

    # Go up two levels to facilitate importing from `mappings` below.
    sys.path[0] = str(Path(sys.path[0]).parents[1])

from utilities.mappings.diaries import DIARIES
from utilities.mappings.quests import QUESTS
from utilities.mappings.skills import NAMES as SKILL_NAMES
from utilities.mappings.stats import NAMES as STAT_NAMES

PATH_SRC = Path(sys.path[0])


class GITracker:
    """Use the Group Ironmen Tracker RuneLite plug-in to get character information.

    This class routes data from the Group Ironmen Tracker plug-in to localhost for
    near-real-time reading. For details on the plug-in's POST endpoints, refer to:
    https://github.com/christoabrown/group-ironmen/tree/master

    Requirements:
        - Group Ironmen Tracker must be installed in RuneLite.
        - A previously-generated (on groupiron.men) group name and auth token.
        - Under the [Group Config] section of the plugin's configuration panel, the
            "Group Name (on the website)" field must match `group_name` exactly.
        - The "Group Token" can be any nonzero number of characters; it does not need
            to be the actual authentication token. If left blank, no POST requests will
            be received on localhost.

    Note that Group Ironmen Tracker plug-in emits "name": "<username>" in its POST
    requests. To maintain consistency, `save_data`, `get_data`, `load_data`, and
    `handle_member_update` use "name". However, `get_username` is provided for external
    reference since it feels more natural to reference a character name as a username.
    As such, both the `name` and `username` attributes are equivalent and set using the
    same method, `set_name`.

    Note additionally that the serialization of quests and diaries is quite intricate.
    Diaries are especially convoluted, and the serialization logic was adapted from:
    https://tinyurl.com/gimdiaries
    """

    def __init__(
        self, username: str = "", group_name: str = "RuneDark", verbose: bool = False
    ) -> None:
        """Spin up a server to handle requests from the Group Ironmen Tracker plug-in.

        Args:
            username (str, optional): The group member's username for referencing
                cached data, if available. Defaults to "".
            group_name (str, optional): The case-sensitive group name as configured on
                groupiron.men. Defaults to "RuneDark".
            verbose (bool, optional): If True, the Flask server prints to the console
                after each request. Defaults to False.
        """
        self.group_name = group_name
        self.app = Flask(__name__)
        self.log = logging.getLogger("werkzeug")
        self.log.disabled = not verbose
        # The raw data recieved from each POST request has the following expected types.
        # When the server is just starting, however, if gi_cache.json exists and has
        # cached state info, that info is serialized, meaning nearly every attribute
        # will have a non-raw, human-readable dict-based data type instead of being an
        # esoteric list of integers. This dynamic type switching is handled by
        # `_serialize_<methodname>` methods and is only relevant just after server
        # startup when this class instance is relaying cached information.
        self.bank: List[int] = []
        self.inventory: List[int] = []
        self.stats: List[int] = []
        # Note that formatted stat names include spaces, unlike skill names.
        self._stat_names = [name.lower().replace(" ", "_") for name in STAT_NAMES]
        self.name: str = username
        self.username = self.name
        self.coordinates: List[int] = []
        self.skills: List[int] = []
        self._skill_names: List[str] = [name.lower() for name in SKILL_NAMES]
        self.interacting: Dict[str, Any] = {}
        self.equipment: List[int] = []
        self.quests: List[int] = []
        self.rune_pouch: List[int] = []
        self.diary_vars: List[int] = []
        self.shared_bank: List[int] = []
        self.cache_path = PATH_SRC / "gi_cache.json"
        if username and self.cache_path.exists():
            self._load_data()
        self.last_updated = None

        @self.app.route(f"/api/group/{self.group_name}/am-i-in-group", methods=["GET"])
        def am_i_in_group() -> Tuple[str, int]:
            """Handle GET requests to check if we are a group member.

            This method effectively checks if the server is running.

            Returns:
                Tuple[Dict[str, Any], int]: Informational message and HTTP status code.
            """
            if request.method == "GET":
                return "We are a group member. Server is running as expected.", 200
            else:
                return "Only GET requests are supported.", 405

        @self.app.route(
            f"/api/group/{self.group_name}/update-group-member", methods=["POST"]
        )
        def update_group_member() -> Tuple[Union[dict, str], int]:
            """Handle POST requests to receive relevant group ironman member data.

            The data received at this endpoint varies in keys with each request. The
            keys change and are typically included when a relevant event occurs,
            prompting an update to the data.

            Returns:
                Tuple[Dict[str, Any], int]: Informational message and HTTP status code.
            """
            if request.method == "POST":
                data = request.json
                setters = {
                    "bank": self._set_bank,
                    "inventory": self._set_inventory,
                    "stats": self._set_stats,
                    "name": self._set_name,
                    "coordinates": self._set_coordinates,
                    "skills": self._set_skills,
                    "interacting": self._set_interacting,
                    "equipment": self._set_equipment,
                    "quests": self._set_quests,
                    "rune_pouch": self._set_rune_pouch,
                    "diary_vars": self._set_diary_vars,
                    "shared_bank": self._set_shared_bank,
                }
                undefined_keys = [key for key in data if key not in setters]
                for key, setter in setters.items():
                    if key in data:
                        setter(data[key])
                if undefined_keys:
                    print(f"Undefined keys: {undefined_keys}")
                self._save_data()
                self._set_last_updated()  # Store the timestamp of the recent update.
                return (
                    jsonify({"status": "success", "undefined_keys": undefined_keys}),
                    200,
                )

            else:
                return "Only POST requests are supported.", 405

        # Start the Flask server as a daemon thread so it runs in the background.
        self.server_thread = threading.Thread(
            target=self.app.run,
            kwargs={"host": "localhost", "port": 9420, "threaded": True},
        )
        self.server_thread.daemon = True
        self.server_thread.start()

    # --- Core Functionality ---
    def _stop(self) -> None:
        """Shutdown the Flask server to stop receiving POST requests."""
        self.server_thread._stop()

    def _load_data(self):
        """Load cached data for the current user if it exists."""
        with open(self.cache_path, "r") as infile:
            try:
                data = json.load(infile)
            except json.JSONDecodeError:
                print(f"JSON could not be read: {self.cache_path}")
                return
            # Map JSON keys to corresponding setter methods.
            setters = {
                "bank": self._set_bank,
                "inventory": self._set_inventory,
                "stats": self._set_stats,
                "name": self._set_name,
                "coordinates": self._set_coordinates,
                "skills": self._set_skills,
                "interacting": self._set_interacting,
                "equipment": self._set_equipment,
                "quests": self._set_quests,
                "rune_pouch": self._set_rune_pouch,
                "diary_vars": self._set_diary_vars,
                "shared_bank": self._set_shared_bank,
            }
            # Call the setter method if the key exists in the data.
            for key, setter in setters.items():
                if key in data:
                    setter(data[key])
            self.last_updated = data.get("last_updated")

    def _serialize_data(self) -> Dict[str, Any]:
        """Retrieve human-readable versions of all currently stored data.

        Returns:
            Dict[str, Any]: A human-readable character state information dictionary.
        """
        return {
            "bank": self._serialize_item_qty_list("bank"),
            "inventory": self._serialize_item_qty_list("inventory"),
            "stats": self._serialize_stats(),
            "name": self.name,
            "coordinates": self._serialize_coordinates(),
            "skills": self._serialize_skills(),
            "interacting": self.interacting,
            "equipment": self._serialize_item_qty_list("equipment"),
            "quests": self._serialize_quests(),
            "rune_pouch": self._serialize_item_qty_list("rune_pouch"),
            "diary_vars": self._serialize_diary_vars(),
            "shared_bank": self._serialize_item_qty_list("shared_bank"),
            "last_updated": self._serialize_last_updated(),
        }

    def _save_data(self):
        """Save character data acquired from a successful POST update as JSON."""
        with open(self.cache_path, "w") as outfile:
            json.dump(self._serialize_data(), outfile, indent=4)

    # --- Serialization Utilities ---
    @staticmethod
    def _is_list_of_ints(obj: object) -> bool:
        """Return whether an object is a list of integers.

        Args:
            obj (object): Any Python object.

        Returns:
            bool: True if the object is a list of integers or is an empty list, False
                otherwise.
        """
        return isinstance(obj, list) and all(isinstance(item, int) for item in obj)

    @staticmethod
    def _is_list_of_dicts(obj: object) -> bool:
        """Return whether an object is a list of dictionaries.

        Args:
            obj (object): Any Python object.

        Returns:
            bool: True if the object is a list of dictionaries or is an empty list,
                False otherwise.
        """
        return isinstance(obj, list) and all(isinstance(item, dict) for item in obj)

    @staticmethod
    def _is_bit_set(num, bit) -> bool:
        """Check if a bit at a specified position in an integer is set (i.e., is 1).

        This method is primarily used as a helper function for `_serialize_diary_vars`.

        Args:
            num (int): The integer to check.
            bit (int): The bit position to check, indexed from 0 (rightmost bit).

        Returns:
            bool: True if the bit at the specified position is set, otherwise False.

        Example:
            >>> is_bit_set(10, 3)
            True

        Explanation:
            - The binary representation of 10 is 1010.
            - Bit positions (right to left) are:
                Position 0: 0
                Position 1: 1
                Position 2: 0
                Position 3: 1
            - Checking the bit at position 3:
                - (1 << 3) shifts binary 1 three positions to the left, resulting in
                    binary 1000 (decimal 8).
                - 10 & 8 performs a bitwise AND between 10 (1010) and 8 (1000),
                    resulting in 1000 (decimal 8).
            - Since 8 is not 0, the function returns True, indicating that the bit at
                position 3 is set.

        Tracking Achievements with Binary Flags:
            - Each achievement in a region is represented by a binary flag (a bit),
                where 1 indicates the achievement is complete and 0 indicates it is not
                complete.
            - For example, if a region has 5 achievements, we represent their collective
                completion status with a string like 10101, where each position in the
                string corresponds to a different achievement.
            - Instead of storing the string of flags directly, we convert it to a
                decimal number. This is more memory-efficient.
        """
        return (num & (1 << bit)) != 0

    @staticmethod
    def _numlist(lo, hi) -> List[int]:
        """
        Returns a list of integers from lo to hi, inclusive.

        This method is primarily used as a helper function for `_serialize_diary_vars`.

        Args:
            lo (int): Starting integer.
            hi (int): Ending integer.

        Returns:
            List[int]: List of integers from lo to hi.

        Example:
            >>> numlist(3, 7)
            [3, 4, 5, 6, 7]
        """
        return list(range(lo, hi + 1))

    # --- Setters ---
    def _set_last_updated(self) -> None:
        """Set the `last_updated` timestamp to the current time since Epoch."""
        self.last_updated = time.time()

    def _set_bank(self, bank_data: List[int]) -> None:
        self.bank = bank_data

    def _set_inventory(self, inventory_data: List[int]) -> None:
        self.inventory = inventory_data

    def _set_stats(self, stats_data: List[int]) -> None:
        self.stats = stats_data

    def _set_name(self, name_data: str) -> None:
        """Set our character's `name` (i.e. `username`) in two equivalent attributes."""
        self.name = name_data
        self.username = self.name

    def _set_coordinates(self, coordinates_data: List[int]) -> None:
        self.coordinates = coordinates_data

    def _set_skills(self, skills_data: List[int]) -> None:
        self.skills = skills_data

    def _set_interacting(self, interacting_data: Dict[str, Any]) -> None:
        self.interacting = interacting_data

    def _set_equipment(self, equipment_data: List[int]) -> None:
        self.equipment = equipment_data

    def _set_quests(self, quests_data: List[int]) -> None:
        self.quests = quests_data

    def _set_rune_pouch(self, rune_pouch_data: List[int]) -> None:
        self.rune_pouch = rune_pouch_data

    def _set_diary_vars(self, diary_vars_data: List[int]) -> None:
        self.diary_vars = diary_vars_data

    def _set_shared_bank(self, shared_bank_data: List[int]) -> None:
        self.shared_bank = shared_bank_data

    # --- Serializers ---
    def _serialize_item_qty_list(
        self,
        attr_name: Literal[
            "bank", "inventory", "equipment", "shared_bank", "rune_pouch"
        ],
    ) -> List[Dict[str, int]]:
        """Serialize a list of item quantities based on the specified attribute name.

        Args:
            attr_name (Literal["bank", "inventory", "equipment", "shared_bank",
                "rune_pouch"]): The name of the attribute to serialize.

        Returns:
            List[Dict[str, int]]: A list of dictionaries, each containing "item_id" and
                "quantity".

        Raises:
            ValueError: If the attribute name is invalid.
        """
        # Dynamically get the attribute based on `attr_name`.
        attribute = getattr(self, attr_name, None)
        if attribute is None:
            raise ValueError(f"Invalid attribute name: {attr_name}")
        if self._is_list_of_dicts(attribute):  # Return if serialized.
            return attribute
        if self._is_list_of_ints(attribute):
            return [
                {"item_id": item_id, "quantity": quantity}
                for item_id, quantity in zip(attribute[::2], attribute[1::2])
            ]

    def _serialize_quests(self) -> List[Dict[str, Any]]:
        """Transform raw quest data into something human-readable.

        Returns:
            List[Dict[str, Any]]: A human-readable list of quests and their
                characteristics.
        """
        if self._is_list_of_dicts(self.quests):
            return self.quests  # Return the data if it has already been serialized.
        quest_state_enum = {0: "STARTED", 1: "NOT_STARTED", 2: "FINISHED"}
        quests_list = QUESTS.copy()  # Don't modify the original mapping.
        for quest, state in zip(quests_list, self.quests):
            quest["state"] = quest_state_enum.get(state, "UNKNOWN")
        return quests_list

    def _serialize_stats(self) -> Dict[str, int]:
        """Transform raw stats data into something human-readable.

        Returns:
            Dict[str, int]: A human-readable list of stats and their current values.
        """
        if self.stats and isinstance(self.stats, dict):
            return self.stats
        return dict(zip(self._stat_names, self.stats))

    def _serialize_skills(self) -> Dict[str, int]:
        """Transform raw skills data into something human-readable.

        Returns:
            Dict[str, int]: Human-readable dictionary mapping skill names to XP amounts.
        """
        if self.skills and isinstance(self.skills, dict):
            return self.skills
        if not self.skills:
            self.skills = [0] * len(self._skill_names)
        return dict(zip(self._skill_names, self.skills))

    def _serialize_coordinates(self) -> Dict[str, int]:
        """Serialize coordinates into a dictionary with keys "x", "y", and "plane".

        Returns:
            Dict[str, int]: A dictionary containing the serialized coordinates.

        Raises:
            ValueError: If `coordinates` is neither a dictionary nor a list of integers.
        """
        if self.coordinates and isinstance(self.coordinates, dict):
            return self.coordinates
        if self._is_list_of_ints(self.coordinates):
            return dict(zip(["x", "y", "plane"], self.coordinates))
        raise ValueError("Invalid coordinates format.")

    def _serialize_last_updated(self) -> str:
        """Serialize the last updated timestamp into a string format.

        Returns:
            str: The last updated timestamp as a formatted string.
        """
        if self.last_updated and isinstance(self.last_updated, str):
            return self.last_updated
        return dt.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")

    def _serialize_diary_vars(self) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """Map decimal values into binary flags to then track diary achievements.

        The logic to this serialization process is convoluted, but makes sense from a
        memory optimization standpoint. Effectively, each region has a number of
        achievements that can be represented as a series of binary flags; 0 for
        incomplete, and 1 for complete.

        Rather than storing the binary flag (e.g. 10010010100001000) as a large number
        or data structure, it is instead converted to decimal when it is sent out in
        POST requests. This method maps those decimal values back into binary flag
        series that correspond to diary achievement progress.

        This method was ported directly from the following code:
        https://tinyurl.com/gimdiaries

        Returns:
            Dict[str, Dict[str, List[Dict[str, Any]]]]: A ledger containing nearly all
                relevant achievement diary info.

        """
        if self.diary_vars and isinstance(self.diary_vars, dict):
            return self.diary_vars
        diary_mappings = {
            "Ardougne": {
                "Easy": [
                    (
                        0,
                        self._numlist(0, 2)
                        + self._numlist(4, 7)
                        + [9]
                        + self._numlist(11, 12),
                    )
                ],
                "Medium": [(0, self._numlist(13, 25))],
                "Hard": [(0, self._numlist(26, 31)), (1, self._numlist(0, 5))],
                "Elite": [(1, self._numlist(6, 13))],
            },
            "Desert": {
                "Easy": [(2, self._numlist(1, 11))],
                #  The idiosyncratic OR condition is represented with a tuple.
                "Medium": [(2, self._numlist(12, 21)), (2, [23]), ((2, 22), (3, 9))],
                "Hard": [(2, self._numlist(24, 31)), (3, self._numlist(0, 1))],
                "Elite": [(3, [2] + self._numlist(4, 8))],
            },
            "Falador": {
                "Easy": [(4, self._numlist(0, 10))],
                "Medium": [(4, self._numlist(11, 25))],
                "Hard": [(4, self._numlist(26, 31)), (5, self._numlist(0, 4))],
                "Elite": [(5, self._numlist(5, 10))],
            },
            "Fremennik": {
                "Easy": [(6, self._numlist(1, 10))],
                "Medium": [(6, self._numlist(11, 15) + self._numlist(17, 20))],
                "Hard": [(6, [21] + self._numlist(23, 30))],
                "Elite": [(6, [31]), (7, self._numlist(0, 4))],
            },
            "Kandarin": {
                "Easy": [(8, self._numlist(1, 11))],
                "Medium": [(8, self._numlist(12, 25))],
                "Hard": [(8, self._numlist(26, 31)), (9, self._numlist(0, 4))],
                "Elite": [(9, self._numlist(5, 11))],
            },
            "Karamja": {
                "Easy": [(i, [5] if i in [23, 30] else [1]) for i in range(23, 33)],
                "Medium": [(i, [1]) for i in range(33, 52)],
                "Hard": [(i, [5] if i == 59 else [1]) for i in range(52, 62)],
                "Elite": [(10, self._numlist(1, 5))],
            },
            "Kourend & Kebos": {
                "Easy": [(11, self._numlist(1, 12))],
                "Medium": [(11, self._numlist(13, 25))],
                "Hard": [(11, self._numlist(26, 31)), (12, self._numlist(0, 3))],
                "Elite": [(12, self._numlist(4, 11))],
            },
            "Lumbridge & Draynor": {
                "Easy": [(13, self._numlist(1, 12))],
                "Medium": [(13, self._numlist(13, 24))],
                "Hard": [(13, self._numlist(25, 31)), (14, self._numlist(0, 3))],
                "Elite": [(14, self._numlist(4, 9))],
            },
            "Morytania": {
                "Easy": [(15, self._numlist(1, 11))],
                "Medium": [(15, self._numlist(12, 22))],
                "Hard": [(15, self._numlist(23, 30)), (16, self._numlist(1, 2))],
                "Elite": [(16, self._numlist(3, 8))],
            },
            "Varrock": {
                "Easy": [(17, self._numlist(1, 14))],
                "Medium": [(17, self._numlist(15, 16) + self._numlist(18, 28))],
                "Hard": [(17, self._numlist(29, 31)), (18, self._numlist(0, 6))],
                "Elite": [(18, self._numlist(7, 11))],
            },
            "Western Provinces": {
                "Easy": [(19, self._numlist(1, 11))],
                "Medium": [(19, self._numlist(12, 24))],
                "Hard": [(19, self._numlist(25, 31)), (20, self._numlist(0, 5))],
                "Elite": [(20, self._numlist(6, 9) + self._numlist(12, 14))],
            },
            "Wilderness": {
                "Easy": [(21, self._numlist(1, 12))],
                "Medium": [(21, self._numlist(13, 16) + self._numlist(18, 24))],
                "Hard": [(21, self._numlist(25, 31)), (22, self._numlist(0, 2))],
                "Elite": [(22, [3, 5] + self._numlist(7, 11))],
            },
        }
        regions = [
            "Ardougne",
            "Desert",
            "Falador",
            "Fremennik",
            "Kandarin",
            "Karamja",
            "Kourend & Kebos",
            "Lumbridge & Draynor",
            "Morytania",
            "Varrock",
            "Western Provinces",
            "Wilderness",
        ]
        difficulties = ["Easy", "Medium", "Hard", "Elite"]
        completion_status = {
            region: {difficulty: [] for difficulty in difficulties}
            for region in regions
        }
        for region, difficulties in diary_mappings.items():
            try:
                for difficulty, tasks in difficulties.items():
                    for var_index, bits in tasks:
                        # Note this special OR condition! Rather than `var_index` being
                        # an int and `bits` being a list of ints, `var_index` and
                        # `bits` are both tuples, each with an int in the first index
                        # and a list of ints in the second.
                        if isinstance(var_index, tuple):
                            completed = any(
                                [
                                    self._is_bit_set(
                                        self.diary_vars[var_index[0]], var_index[1]
                                    ),
                                    self._is_bit_set(self.diary_vars[bits[0]], bits[1]),
                                ]
                            )
                            completion_status[region][difficulty].append(completed)
                            continue
                        for bit in bits:
                            if region == "Karamja":  # This diary is uniquely defined.
                                completed = self.diary_vars[var_index] == bit
                            else:
                                completed = self._is_bit_set(
                                    self.diary_vars[var_index], bit
                                )
                            completion_status[region][difficulty].append(completed)
            except Exception:
                # This exception arises because `DIARIES` has incomplete information.
                # print(f"Unexpected exception while serializing diaries.")
                continue
        serialized_diary = DIARIES.copy()
        for region, difficulties in completion_status.items():
            for difficulty, statuses in difficulties.items():
                for i, status in enumerate(statuses):
                    try:
                        status = "COMPLETE" if status else "INCOMPLETE"
                        serialized_diary[region][difficulty][i]["status"] = status
                    except IndexError:
                        serialized_diary[region][difficulty].append({"task": "UNKNOWN"})
        return serialized_diary

    # --- Regular Method Subroutines ---
    def _get_xp_to_lvl(self, lvl: int) -> int:
        """Get the required amount of XP to reach the given level.

        This calculation is taken straight from the OSRS wiki. See:
        https://oldschool.runescape.wiki/w/Experience#Formula

        Args:
            lvl (int): The level to calculate the amount of XP to reach.

        Returns:
            int: The amount of XP required to reach `lvl` in any OSRS skill.
        """
        tot_xp = 0  # See the raw XP table at: https://tinyurl.com/yckxmz2j
        for ell in range(1, lvl):  # `ell` must start at 1.
            tot_xp += math.floor(ell + 300 * (2 ** (ell / 7)))
        return math.floor(0.25 * tot_xp)

    def _get_lvl_from_xp(self, xp: int) -> int:
        """Determine the corresponding skill level for a given amount of XP.

        Args:
            xp (int): The amount of XP associated with any given OSRS skill level.

        Returns:
            int: The 1-to-99 skill level corresponding to the given amount of XP.
        """
        tot_xp = 0
        # `ell` is an intermediate level that must start at 1 to follow the XP table.
        for ell in range(1, 127):  # See: https://tinyurl.com/yckxmz2j
            tot_xp += math.floor(ell + 300 * (2 ** (ell / 7)))
            if math.floor(0.25 * tot_xp) > xp:
                return ell
            ell += 1
        return ell

    # --- Getters ---
    def get_bank(self) -> List[Dict[str, int]]:
        """Get a serialized list of items and quantities from the bank.

        Returns:
            List[Dict[str, int]]: A list of dictionaries, each containing "item_id" and
                "quantity" from the bank.
        """
        return self._serialize_item_qty_list("bank")

    def get_inventory(self) -> List[Dict[str, int]]:
        """Get a serialized list of items and quantities from our character's inventory.

        Returns:
            List[Dict[str, int]]: A list of dictionaries, each containing "item_id" and
                "quantity" from our inventory.
        """
        return self._serialize_item_qty_list("inventory")

    def get_stats(self) -> Dict[str, int]:
        """Get current character stats like hitpoints and prayer.

        Returns:
            Dict[str, int]: A dictionary of player stats.
        """
        return self._serialize_stats()

    def get_username(self) -> str:
        """Get the username of the account currently logged in.

        Returns:
            str: The up-to-12-character username associated with the logged-in account.
        """
        return self.name

    def get_coordinates(self) -> List[int]:
        """Get coordinates describing our characters position, measured in world tiles.

        Note that the "plane" key is effectively the z-axis or "dungeon floor".

        Returns:
            Dict[str, int]: A dictionary with "x", "y", and "plane" keys.
        """
        return self._serialize_coordinates()

    def get_skills(self) -> Dict[str, int]:
        """Get a summary of XP levels for each of the 23 skills.

        Returns:
            Dict[str, int]: A dictionary of player skills.
        """
        return self._serialize_skills()

    def get_interacting(self) -> Dict[str, Any]:
        """Get info about our character's most recent interaction with another entity.

        Returns:
            Dict[str, Any]: A dictionary containing details about the most recent
                interaction, such as the "name" (i.e. the associated NPC), "scale",
                "ratio", and "location".
        """
        return self.interacting

    def get_equipment(self) -> List[Dict[str, int]]:
        """Get a serialized list of items and quantities from our character's equipment.

        Returns:
            List[Dict[str, int]]: A list of dictionaries, each containing "item_id" and
                "quantity" from our equipment (i.e. what our character is actively
                wearing in the Worn Equipment tab).
        """
        return self._serialize_item_qty_list("equipment")

    def get_quests(self) -> List[Dict[str, Any]]:
        """Get detailed data on the status of our character's questing.

        Returns:
            List[Dict[str, Any]]: A human-readable list of quests and their
                characteristics.
        """
        return self._serialize_quests()

    def get_rune_pouch(self) -> List[int]:
        """Get a serialized list of items and quantities in our character's rune pouch.

        Returns:
            List[Dict[str, int]]: A list of dictionaries, each containing "item_id" and
                "quantity" from our character's rune pouch.
        """
        return self._serialize_item_qty_list("rune_pouch")

    def get_diary_vars(self) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """Get a detailed ledger describing the state of our characters diaries.

        Returns:
            Dict[str, Dict[str, List[Dict[str, Any]]]]: Detailed information about our
                character's achievement diary progress for all regions.
        """
        return self._serialize_diary_vars()

    def get_shared_bank(self) -> List[Dict[str, int]]:
        """Get a list of items and quantities from a group ironman shared bank.

        Returns:
            List[Dict[str, int]]: A list of dictionaries, each containing "item_id" and
                "quantity" from the bank.
        """
        return self._serialize_item_qty_list("shared_bank")

    def get_last_updated(self) -> str:
        """Get the timestamp associate with the most recent successful POST.

        Returns:
            str: Human-readable timestamp, effectively describing the recency of the
                current data.
        """
        return self._serialize_last_updated()

    # --- Regular Methods (which all include relevant `_serialize_<attr>` methods) ---
    def get_combat_lvl(self) -> int:
        """Get our character's combat level.

        See: https://oldschool.runescape.wiki/w/Combat_level#Mathematics

        Returns:
            int: Our character's overall combat level (i.e. power level).
        """
        self.skills = self._serialize_skills()
        base = (1 / 4) * (
            self.get_skill_lvl("defence")
            + self.get_skill_lvl("hitpoints")
            + math.floor(self.get_skill_lvl("prayer") * (1 / 2))
        )
        melee = (13 / 40) * (
            self.get_skill_lvl("attack") + self.get_skill_lvl("strength")
        )
        ranged = (13 / 40) * math.floor(self.get_skill_lvl("ranged") * (3 / 2))
        mage = (13 / 40) * math.floor(self.get_skill_lvl("magic") * (3 / 2))
        combat_lvl = math.floor(base + max(melee, ranged, mage))
        return combat_lvl

    def get_skill_lvl(self, skill: str) -> int:
        """Return our character's current level in the provided skill.

        Args:
            skill (str): The name of the skill (e.g. "fishing").

        Returns:
            int: The level of the skill, from 1 to 99.
        """
        self.skills = self._serialize_skills()
        xp = self.skills.get(skill.lower(), 0)
        return self._get_lvl_from_xp(xp)

    def get_quests_summary(self) -> Dict[str, int]:
        """Get a summary of our character's overall questing status.

        Returns:
            Dict[str, int]: A breakdown of the number of quests our character has
                started, not started, and finished.
        """
        self.quests = self._serialize_quests()
        quest_states = [quest["state"] for quest in self.quests]
        num_started = quest_states.count("STARTED")
        num_not_started = quest_states.count("NOT_STARTED")
        num_finished = quest_states.count("FINISHED")
        summary = {
            "num_started": num_started,
            "num_not_started": num_not_started,
            "num_finished": num_finished,
        }
        return summary


if __name__ == "__main__":
    from utilities import settings

    USERNAME = settings.get("username")

    api_g = GITracker(username=USERNAME, verbose=True)
    for _ in range(5):
        if True:
            msg = (
                # f"\n{'username:':>20} {api_g.get_username()}"
                f"\n{'combat level:':>20} {api_g.get_combat_lvl()}"
                f"\n{'skills:':>20} {api_g.get_skills()}"
                # f"\n{'quests summary:':>20} {api_g.get_quests_summary()}"
            )

        print(msg)
        time.sleep(3)
