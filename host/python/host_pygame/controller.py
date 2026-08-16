"""Controller stub (no devices on host MVP)."""


def init():
    return None


def quit():
    return None


def get_count():
    return 0


def get_init():
    return True


def add_mapping(mapping):
    return None


def add_mappings(mapping_file):
    return None


def get_axis_from_string(name):
    return 0


def get_button_from_string(name):
    return 0


def get_string_for_axis(axis):
    return f"axis{axis}"


def get_string_for_button(button):
    return f"button{button}"


class Controller:
    def __init__(self, id):
        self.id = id
        self.instance_id = id

    def init(self):
        return None

    def quit(self):
        return None

    def get_init(self):
        return False

    def get_axis(self, axis):
        return 0.0

    def get_button(self, button):
        return False

    def get_name(self):
        return ""

    def is_controller(self):
        return False

    def get_guid_string(self):
        return ""
