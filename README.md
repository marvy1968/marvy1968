# vm-link

Jobs for the Marv VM. Add `jobs/<name>.job` with one command line; the VM writes `results/<name>.txt`.
Commands: `tests`, `update`, `status`, `logs <unit> [lines]`, `show <path under state/>`,
`marv <command> [args]` (run needs --dry-run; --send/--watch refused). See deploy/vm_link.py on the bot branch.
