# freeze

Archive an idle project folder into a single zip **that lives inside it**, so the
folder stays visible as a marker of what used to be there.

```
C:\dev\old-project\        ->   C:\dev\old-project\
  src/ node_modules/ ...          old-project.zip
```

`freeze unzip old-project` puts it all back and deletes the zip.

Nothing is deleted until the archive has been verified: every entry is
CRC-checked and the file count and total size are compared against the folder.
If a locked file interrupts the delete, re-running `freeze zip` finds the good
archive and finishes the job instead of re-zipping.

## Install

```bash
pip install "freeze[all]"                 # CLI, interactive picker, MCP server
pip install freeze                        # core only, no picker and no MCP
pipx install "freeze[all]"                # or uv tool install, to keep it off your global env
```

From a checkout:

```bash
pip install -e ".[all]"
```

## Use

```bash
freeze                      # interactive picker, ranked by best candidate
freeze scan                 # list folders: size, idle days, git state
freeze scan --min-size 500MB --idle 60d
freeze zip old-project      # archive it, then delete the contents
freeze unzip old-project    # restore in place
```

Where it works:

- the current directory when it is inside the default root (`C:\dev`)
- otherwise the default root itself
- `--root D:\projects` overrides both, and `FREEZE_ROOT` changes the default

Only the direct children of that folder are candidates.

`scan` ranks by **size × idle days**, so a big folder untouched for a year
comes before a big one you edited yesterday. Idle time is the newest file
anywhere in the tree.

## Safety

- The archive is verified before anything is deleted.
- Git repos with uncommitted or unpushed work are flagged, then archived anyway.
  `.git` goes into the zip, so history survives.
- Symlinks and Windows junctions are skipped, not followed, and are listed in
  the output.
- `unzip` refuses to run when the folder holds anything besides the archive,
  and rejects entries whose paths point outside it.
- Empty directories are preserved through the round trip.
- freeze never archives its own checkout.

## MCP server

```bash
claude mcp add freeze -- freeze-mcp
```

Or in `claude_desktop_config.json`:

```json
{ "mcpServers": { "freeze": { "command": "freeze-mcp" } } }
```

Tools: `scan`, `suggest`, `zip`, `unzip`. `zip` is gated: without
`confirm=true` it only reports what would happen, so an agent cannot delete a
project by accident.

## Notes for synced folders

If the root is inside Dropbox, OneDrive or Syncthing, archiving deletes the
files on every synced machine and then copies the zip to all of them. That is
the intent, but it is a big transfer. For Syncthing, add `(?d)*.zip.partial` to
`.stignore` on each machine so a half-written archive never syncs.

## Tests

```bash
python tests/test_core.py
```

MIT licensed.
