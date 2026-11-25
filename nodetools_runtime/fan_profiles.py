"""Ordered IPMI raw presets for existing OEM adapters and local models."""

from configparser import ConfigParser, Error
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re

from . import model, scan
from .errors import CapUnsupported, NodetoolsError, TransportError

ACTIONS = ("auto", "max", "low", "opt")
MATCH = ("platform", "oem", "role", "model")
IPMI_OPTIONS = ("ipmi_timeout", "ipmi_retries")
BUILTIN = Path(__file__).with_name("fan-profiles.ini")
BYTE = re.compile(r"(?:0[xX][0-9a-fA-F]{1,2}|[0-9]{1,3})")


@dataclass(frozen=True)
class Profile:
    name: str
    match: tuple
    actions: dict
    ipmi_timeout: int
    ipmi_retries: int

    def sequence(self, action):
        if action not in self.actions:
            raise CapUnsupported("fan profile {} has no {} action".format(self.name, action))
        return self.actions[action]


def _read_ini(path):
    parser = ConfigParser(interpolation=None, inline_comment_prefixes=("#", ";"))
    try:
        with Path(path).open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except (Error, OSError, UnicodeError) as exc:
        raise NodetoolsError("cannot load fan profiles {}: {}".format(path, exc)) from exc
    unknown = parser.defaults().keys() - set(IPMI_OPTIONS)
    if unknown:
        raise NodetoolsError("{} [DEFAULT]: unknown keys {}".format(path, ", ".join(sorted(unknown))))
    return parser


def load(path, local=False):
    parser = _read_ini(path)
    if not parser.sections():
        raise NodetoolsError("{} requires profile sections".format(path))
    inherited = _read_ini(BUILTIN).defaults() if local else {}
    profiles = {}
    seen = set()
    for section in parser.sections():
        name = section.partition(":")[2].strip()
        if not section.startswith("profile:") or not name or name in profiles:
            raise NodetoolsError("{}: invalid profile section [{}]".format(path, section))
        values = parser[section]
        bindings = tuple(values.get(key, "").strip().casefold() for key in MATCH) if local else ()
        if local and (not all(bindings) or bindings in seen
                      or bindings[0] not in ("hgx", "nvl")
                      or bindings[2] not in ("compute", "powershelf")):
            raise NodetoolsError("{} [{}]: invalid or duplicate model match".format(path, section))
        seen.add(bindings)
        try:
            ipmi_timeout = int(values.get("ipmi_timeout", inherited.get("ipmi_timeout", 8)))
            ipmi_retries = int(values.get("ipmi_retries", inherited.get("ipmi_retries", 1)))
            if ipmi_timeout < 1 or ipmi_retries < 1:
                raise ValueError
        except ValueError:
            raise NodetoolsError(
                "{} [{}]: ipmi_timeout and ipmi_retries must be positive integers".format(path, section)
            ) from None
        actions = {}
        for key, value in values.items():
            if key in IPMI_OPTIONS or (local and key in MATCH):
                continue
            if key not in ACTIONS:
                raise NodetoolsError("{} [{}]: unknown key {}".format(path, section, key))
            rows = []
            for line in value.splitlines():
                tokens = line.split()
                if not tokens:
                    continue
                if len(tokens) < 2 or any(not BYTE.fullmatch(token) for token in tokens):
                    raise NodetoolsError("{} [{}] {}: expected raw bytes".format(path, section, key))
                row = tuple(int(token, 16 if token.lower().startswith("0x") else 10) for token in tokens)
                if any(byte > 255 for byte in row):
                    raise NodetoolsError("{} [{}] {}: byte outside 0–255".format(path, section, key))
                rows.append(row)
            if not rows:
                raise NodetoolsError("{} [{}]: empty {} sequence".format(path, section, key))
            actions[key] = tuple(rows)
        if not actions:
            raise NodetoolsError("{} [{}]: no actions".format(path, section))
        profiles[name] = Profile(name, bindings, actions, ipmi_timeout, ipmi_retries)
    return profiles


@lru_cache(maxsize=1)
def bundled():
    return load(BUILTIN)


def for_model(device, profiles):
    live = scan.read_identity(device.redfish, device.vars)
    product = str(live.get("model") or "").strip().casefold()
    key = (model.platform_for(device), model.odm_for(device), device.role, product)
    return next((profile for profile in profiles.values() if profile.match == key), None)


def execute(device, profile, action):
    ipmi = device.ipmi
    if not ipmi:
        raise CapUnsupported("ipmitool unavailable for {}".format(device.name))
    rows = profile.sequence(action)
    ipmi.retry_interval = profile.ipmi_timeout
    ipmi.retries = profile.ipmi_retries
    for step, row in enumerate(rows, 1):
        try:
            ipmi.raw(*row)
        except NodetoolsError as exc:
            raise TransportError(
                "fan profile {} {} step {}/{} failed ({} steps completed): {}".format(
                    profile.name, action, step, len(rows), step - 1, exc
                )
            ) from exc
    return "ok"


def preview(profile, action):
    rows = profile.sequence(action)
    return [
        "{} {} step {}/{}: ipmitool raw {}".format(
            profile.name, action, step, len(rows), " ".join("0x{:02x}".format(byte) for byte in row)
        )
        for step, row in enumerate(rows, 1)
    ]
