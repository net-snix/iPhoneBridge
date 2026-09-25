"""US keyboard to USB HID usage mapping shared by CLI action paths."""

NAMED_KEYS = {"enter": 0x28, "tab": 0x2B, "escape": 0x29, "backspace": 0x2A,
              "delete": 0x4C, "left": 0x50, "right": 0x4F, "up": 0x52,
              "down": 0x51, "home": 0x4A, "end": 0x4D, "pageup": 0x4B,
              "pagedown": 0x4E}
SHIFT = 0xE1
SHIFTED_US = dict(zip('~!@#$%^&*()_+{}|:"<>?', '`1234567890-=[]\\;\',./', strict=True))
BASE = {chr(ord('a') + index): 4 + index for index in range(26)}
BASE.update({char: 0x1E + index for index, char in enumerate("1234567890")})
BASE.update(dict(zip("\n\t -=[]\\;'`,./", (0x28, 0x2B, 0x2C, 0x2D, 0x2E, 0x2F, 0x30,
                                                   0x31, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38), strict=True)))


def character(char):
    shifted = "A" <= char <= "Z" or char in SHIFTED_US
    base = SHIFTED_US.get(char, char.lower() if shifted else char)
    return BASE[base], shifted
