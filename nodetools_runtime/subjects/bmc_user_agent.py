"""Device-side local-IPMI user provisioning over authenticated OS SSH."""

import json
import fcntl
import re
import shlex
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
    operation = payload.get("operation", "upsert")
    if operation not in ("upsert", "modify", "delete", "list", "verify"):
        raise ValueError("unknown account operation")
    if payload["channel"] is not None and not 0 <= payload["channel"] <= 15:
        raise ValueError("invalid channel")
    if operation == "list":
        return
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,15}", payload["name"]):
        raise ValueError("username must be 1-16 ASCII letters, digits, _, . or -")
    if payload["name"].lower() == "root" or (payload["id"] is not None and not 2 <= payload["id"] <= 63):
        raise ValueError("root and reserved user ID 1 cannot be modified")
    if payload["channel"] is not None and not 0 <= payload["channel"] <= 15:
        raise ValueError("channel must be 0-15")
    if payload.get("sol") and payload["role"] != "compute":
        raise ValueError("SOL is compute-only")
    password = payload.get("password")
    if payload.get("apply") and operation in ("upsert", "modify") and (not isinstance(password, str)
            or not 1 <= len(password.encode()) <= 20
            or any(ord(c) < 32 or ord(c) == 127 for c in password)):
        raise ValueError("password must be 1-20 UTF-8 bytes without control characters")


def provision(payload, call=subprocess.run):
    validate(payload)
    completed = []
    trace = []

    def answer(**values):
        return dict(trace=trace, **values)
    deadline = time.monotonic() + payload.get("seconds", 30)

    def ipmi(stage, *args):
        if payload.get("debug"):
            safe = list(map(str, args))
            if safe[:3] == ["user", "set", "password"] and len(safe) > 4:
                safe[4] = "<redacted>"
            trace.append(shlex.join(["ipmitool", "-I", "open", *safe]))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(stage + ": deadline exceeded")
        try:
            result = call(["ipmitool", "-I", "open", *map(str, args)],
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=remaining)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError(stage + ": execution failed; inspect before retry") from None
        if payload.get("debug"):
            trace[-1] += " -> exit " + str(result.returncode)
        if result.returncode:
            raise RuntimeError(stage + ": IPMI exit " + str(result.returncode))
        return result.stdout

    try:
        ident, channel, name = payload["id"], payload["channel"], payload["name"]
        if channel is None:
            for candidate in [1, 0, *range(2, 15)]:
                try:
                    info = ipmi("detect channel", "channel", "info", candidate)
                except RuntimeError as exc:
                    if "IPMI exit" in str(exc):
                        continue
                    raise
                if re.search(r"Channel Medium Type\s*:\s*802\.3", info):
                    channel = candidate
                    break
            if channel is None:
                raise ValueError("no usable management channel found")
        before = users(ipmi("read users", "user", "list", channel))
        operation = payload.get("operation", "upsert")
        if operation == "list":
            rows = [dict(id=k, **v) for k, v in before.items()
                    if v["name"] and (not name or v["name"] == name)]
            return answer(ok=True, applied=False, completed=[], users=rows)
        named = [k for k, v in before.items() if v["name"] == name]
        if len(named) > 1:
            raise ValueError("duplicate username in IPMI user table")
        if operation in ("modify", "verify") and not named:
            raise ValueError("user does not exist")
        if operation == "delete":
            if not named:
                return answer(ok=True, applied=False, completed=[], absent=True)
            ident = named[0]
            if ident < 2:
                raise ValueError("reserved account cannot be deleted")
            steps = [
                ("disable user", ["user", "disable", ident]),
                ("revoke access", ["channel", "setaccess", channel, ident,
                 "callin=off", "ipmi=off", "link=off", "privilege=15"]),
                ("clear name", ["user", "set", "name", ident, ""]),
            ]
            if not payload.get("apply"):
                return answer(ok=True, applied=False, completed=[], plan=[v[0] for v in steps])
            for stage, argv in steps:
                ipmi(stage, *argv)
                completed.append(stage)
            after = users(ipmi("read back users", "user", "list", channel))
            if ident in after and (after[ident]["name"] or after[ident]["link"] or after[ident]["ipmi"]):
                raise RuntimeError("user deletion failed readback")
            return answer(ok=True, applied=True, completed=completed, deleted=True)
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
        if operation == "modify":
            plan = [entry for entry in plan if entry[0] == "set password"]
        if payload.get("sol") and operation != "modify":
            plan.append(("enable SOL channel", ["sol", "set", "enabled", "true", channel]))
            plan.append(("enable SOL", ["sol", "payload", "enable", channel, ident]))
        if payload.get("apply"):
            for stage, argv in plan:
                ipmi(stage, *argv)
                completed.append(stage)
            if payload.get("sol") and operation != "modify":
                info = ipmi("read back SOL channel", "sol", "info", channel)
                access = ipmi("read back SOL user", "sol", "payload", "status", channel, ident)
                if (not re.search(r"(?mi)^\s*Enabled\s*:\s*true\s*$", info)
                        or not re.search(r"\bis enabled\s*$", access)):
                    raise RuntimeError("SOL channel or user permission failed readback")
            after = users(ipmi("read back users", "user", "list", channel))[ident]
            if operation == "modify" and after != before[ident]:
                raise RuntimeError("password update changed account permissions")
            if (operation != "modify" and (after["name"] != name or not all(after[k] for k in ("callin", "link", "ipmi"))
                    or after["privilege"] != "ADMINISTRATOR")):
                raise RuntimeError("IPMI user permissions failed readback")
        return answer(ok=True, applied=bool(payload.get("apply")), completed=completed,
                    plan=[stage for stage, _ in plan], slot=ident, name=name,
                    sol=("enabled" if payload.get("apply") else "planned") if payload.get("sol") else "skipped")
    except (ValueError, RuntimeError, KeyError) as exc:
        return answer(ok=False, error=str(exc), completed=completed)


def main():
    try:
        payload = json.load(sys.stdin)
        with open("/run/lock/nodetools-bmc-users.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = provision(payload)
    except Exception:
        result = dict(ok=False, error="invalid provisioning request", completed=[])
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
