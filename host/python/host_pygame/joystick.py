"""Joystick stub — import succeeds, empty devices."""


def init():
    return None


def quit():
    return None


def get_count():
    return 0


class Joystick:
    def __init__(self, id):
        self.id = id

    def init(self):
        return None

    def quit(self):
        return None

    def get_name(self):
        return ""

    def get_numaxes(self):
        return 0

    def get_numbuttons(self):
        return 0

    def get_numhats(self):
        return 0
