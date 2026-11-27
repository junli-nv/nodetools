"""Device-side local-IPMI user provisioning over authenticated OS SSH."""

import json
import re
import subprocess
import sys
import time

ROW = re.compile(r"^\s*(\d+)\s+(.*?)\s+(true|false)\s+(true|false)\s+(true|false)\s+(.+?)\s*$", re.I)


def users(text):
    result = {}
    for line in text.splitlines():
        if not re.match(r"^\s*\d+\s", line):
            continue
        match = ROW.fullmatch(line)
        if not match:
            raise ValueError("unrecognized IPMI user table")
        ident, name, callin, link, ipmi, privilege = match.groups()
        ident = int(ident)
        if ident in result:
            raise ValueError("duplicate IPMI user ID")
        result[ident] = dict(name=name.strip(), callin=callin.lower() == "true",
                             link=link.lower() == "true", ipmi=ipmi.lower() == "true",
                             privilege=privilege.strip())
    if not result:
        raise ValueError("empty IPMI user table")
    return result


def validate(payload):
    if payload["role"] not in ("compute", "nvswitch"):
        raise ValueError("only compute and nvswitch are supported")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,15}", payload["name"]):
        raise ValueError("username must be 1-16 ASCII letters, digits, _, . or -")
    if payload["name"].lower() == "root" or (payload["id"] is not None and not 2 <= payload["id"] <= 63):
        raise ValueError("root and reserved user ID 1 cannot be modified")
    if not 0 <= payload["channel"] <= 15:
        raise ValueError("channel must be 0-15")
    if payload.get("sol") and payload["role"] != "compute":
        raise ValueError("SOL is compute-only")
    password = payload.get("password")
    if payload.get("apply") and (not isinstance(password, str)
            or not 1 <= len(password.encode()) <= 20
            or any(ord(c) < 32 or ord(c) == 127 for c in password)):
        raise ValueError("password must be 1-20 UTF-8 bytes without control characters")


def provision(payload, call=subprocess.run):
    validate(payload)
    completed = []
    deadline = time.monotonic() + payload.get("seconds", 30)

    def ipmi(stage, *args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(stage + ": deadline exceeded")
        try:
            result = call(["ipmitool", "-I", "open", *map(str, args)],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=remaining)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError(stage + ": execution failed; inspect before retry") from None
        if result.returncode:
            raise RuntimeError(stage + ": IPMI exit " + str(result.returncode))
        return result.stdout

    try:
        ident, channel, name = payload["id"], payload["channel"], payload["name"]
        before = users(ipmi("read users", "user", "list", channel))
        named = [k for k, v in before.items() if v["name"] == name]
        if len(named) > 1:
            raise ValueError("duplicate username in IPMI user table")
        if ident is None:
            empty = [k for k, v in before.items() if k >= 2 and not v["name"]]
            ident = named[0] if named else 8 if 8 in empty else next(iter(empty), None)
        if ident is not None and ident < 2:
            raise ValueError("reserved user ID cannot be modified")
        if ident not in before:
            raise ValueError("requested user ID is not listed by this BMC")
        if before[ident]["name"] not in ("", name):
            raise ValueError("requested user ID belongs to another account")
        if any(k != ident and v["name"] == name for k, v in before.items()):
            raise ValueError("username already exists at user ID " + str(named[0]))
        plan = [
            ("set name", ["user", "set", "name", ident, name]),
            ("set password", ["user", "set", "password", ident, payload.get("password", ""),
                              20 if len((payload.get("password") or "").encode()) > 16 else 16]),
            ("enable user", ["user", "enable", ident]),
            ("set access", ["channel", "setaccess", channel, ident,
                            "callin=on", "ipmi=on", "link=on", "privilege=4"]),
        ]
        if payload.get("sol"):
            plan.append(("enable SOL channel", ["sol", "set", "enabled", "true", channel]))
            plan.append(("enable SOL", ["sol", "payload", "enable", channel, ident]))
        if payload.get("apply"):
            for stage, argv in plan:
                ipmi(stage, *argv)
                completed.append(stage)
            if payload.get("sol"):
                info = ipmi("read back SOL channel", "sol", "info", channel)
                access = ipmi("read back SOL user", "sol", "payload", "status", channel, ident)
                if (not re.search(r"(?mi)^\s*Enabled\s*:\s*true\s*$", info)
                        or not re.search(r"\bis enabled\s*$", access)):
                    raise RuntimeError("SOL channel or user permission failed readback")
            after = users(ipmi("read back users", "user", "list", channel))[ident]
            if (after["name"] != name or not all(after[k] for k in ("callin", "link", "ipmi"))
                    or after["privilege"] != "ADMINISTRATOR"):
                raise RuntimeError("IPMI user permissions failed readback")
        return dict(ok=True, applied=bool(payload.get("apply")), completed=completed,
                    plan=[stage for stage, _ in plan], slot=ident, name=name,
                    sol=("enabled" if payload.get("apply") else "planned") if payload.get("sol") else "skipped")
    except (ValueError, RuntimeError, KeyError) as exc:
        return dict(ok=False, error=str(exc), completed=completed)


def main():
    try:
        result = provision(json.load(sys.stdin))
    except Exception:
        result = dict(ok=False, error="invalid provisioning request", completed=[])
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
