#!/bin/bash
# Author: Junli Zhang <junliz@nvidia.com>
# Generic editable credentials template; no BCM command is executed.
exec python3 - "$@" <<'PY'
import argparse
import sys
import os
from pathlib import Path

parser = argparse.ArgumentParser(prog='gen_passwd.sh', description="Create an editable passwd.sh template without querying BCM.")
parser.formatter_class = argparse.RawDescriptionHelpFormatter
parser.epilog = 'Examples\n  ./gen_passwd.sh\n  ./gen_passwd.sh -o /tmp/new-passwd.sh'
parser.add_argument("-o", "--output", default="passwd.sh")
parser.add_argument("-x", "--debug", action="store_true", help="show diagnostics on stderr")
parser.epilog += '\n  ./gen_passwd.sh -x -o /tmp/passwd-debug.sh'
args = parser.parse_args()
def trace(message):
    if args.debug:
        print("[gen_passwd] " + message, file=sys.stderr, flush=True)

template = """#!/bin/bash
# Fill from your own records, or use the optional BCM lookup paths below.
# This file can be written manually. nodetools imports assignments without running it.

# cmsh -> category gb200 -> bmcsettings (compute BMC, also used for IPMI)
# Substitute the actual compute category name for your site.
export bmc_username=root
export bmc_password='CHANGE_ME_COMPUTE'
export ipmi_bmc_username=${bmc_username}
export ipmi_bmc_password=${bmc_password}

# cmsh -> device/category nvswitch -> bmcsettings
export nvswitch_bmc_user=root
export nvswitch_bmc_pass='CHANGE_ME_NVSWITCH_BMC'

# cmsh -> device nvswitch -> accesssettings
export nvswitch_os_user=admin
export nvswitch_os_pass='CHANGE_ME_NVOS'

# cmsh -> device powershelf -> bmcsettings
export powershelf_bmc_user=root
export powershelf_bmc_pass='CHANGE_ME_POWERSHELF'

# Legacy IB/SPX fields, retained for downstream scripts.
# Current ibsw*/spxsw* tools use OpenSSH configuration/keys or SSHPASS.
export ibswitch_os_user=admin
export ibswitch_os_pass='CHANGE_ME_IB'
export spx_switch_os_user=cumulus
export spx_switch_os_pass='CHANGE_ME_SPX'
"""
trace("creating credentials template; output=" + args.output + " mode=0600")
try:
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(template)
    print("wrote editable credentials template to " + str(Path(args.output)))
except FileExistsError:
    parser.exit(1, "gen_passwd: output already exists; choose another -o path or edit the existing file\n")
except OSError as exc:
    parser.exit(1, "gen_passwd: {}\n".format(exc))
PY
