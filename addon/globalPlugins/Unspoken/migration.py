"""Configuration migration for legacy Unspoken settings."""

CONF_SPEC = {
    "theme": 'string(default="default")',
    "roleAnnouncement": 'option("sounds", "soundsAndSpeech", "speechOnly", default="sounds")',
    "reverb": 'option("none", "smallRoom", "mediumRoom", "hall", default="smallRoom")',
    "silenceDuringSayAll": "boolean(default=False)",
}

_OLD_KEYS = (
    "sayAll",
    "speakRoles",
    "noSounds",
    "HRTF",
    "volumeAdjust",
    "Reverb",
    "RoomSize",
    "Damping",
    "WetLevel",
    "DryLevel",
    "Width",
)


def migrate(section) -> None:
    """Migrate legacy settings by mutating ``section`` in place.

    ``section`` may be a plain dict or any dict-like object that supports key
    membership tests and item access, assignment, and deletion. The function
    returns ``None``. If no legacy keys are present, the mapping is left
    completely unchanged.
    """
    if not any(key in section for key in _OLD_KEYS):
        return

    no_sounds = section["noSounds"] if "noSounds" in section else False
    speak_roles = section["speakRoles"] if "speakRoles" in section else False
    if no_sounds:
        section["roleAnnouncement"] = "speechOnly"
    elif speak_roles:
        section["roleAnnouncement"] = "soundsAndSpeech"
    else:
        section["roleAnnouncement"] = "sounds"

    if "sayAll" in section and section["sayAll"]:
        section["silenceDuringSayAll"] = True

    if "Reverb" in section:
        section["reverb"] = "smallRoom" if section["Reverb"] else "none"

    for key in _OLD_KEYS:
        if key in section:
            del section[key]
