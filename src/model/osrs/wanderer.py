from model.osrs.osrs_bot import OSRSBot
from utilities.mappings import locations as loc
from utilities.walker import Walker


class OSRSWanderer(OSRSBot):
    def __init__(self):
        super().__init__(
            bot_title="Wanderer",
            description=(
                "Given a path, global coordinates of a destination, or the"
                " name of a well-known destination, walk there."
            ),
        )
        self.dest = "GRAND_EXCHANGE"
        self.dest_title = "Grand Exchange"
        self.options_set = True

    def create_options(self):
        locations = [name for name in vars(loc) if not name.startswith("__")]
        self.options_builder.add_dropdown_option("dest", "Destination:", locations)

    def save_options(self, options: dict):
        for option in options:
            if option == "dest":
                self.dest = options[option]
                self.dest_title = self.dest.lower().replace("_", " ").title()
                self.log_msg(f"Planned destination: {self.dest_title}")
        self.log_msg("Options set successfully.")
        self.options_set = True

    def main_loop(self):
        """Travel to a given location by foot.

        For this to work as intended:
            1. The destination tuples or list of tuple waypoints must be appropriately
                formatted. See `utilities.locations` for examples.
            2. Screen dimmers like F.lux or Night Light on Windows should be disabled
                since our bot is highly sensitive to colors.
        """
        while True:
            self.log_msg(f"Walking to {self.dest_title}...")
            walker = Walker(self, dest_square_side_length=4)
            dest_value = getattr(loc, self.dest)
            if isinstance(dest_value, list):
                arrived = walker.walk(dest_value)
            elif isinstance(dest_value, tuple):
                arrived = walker.walk_to(self.dest, host="dax")
            else:
                msg = (
                    "Destination must be a tuple of ints or a list of tuples of"
                    f" ints: {self.dest}"
                )
                raise ValueError(msg)
            prefix = "Arrived" if arrived else "Did not arrive"
            self.log_msg(f"{prefix} at {self.dest_title}.")
            self.stop()
            self.stop()
