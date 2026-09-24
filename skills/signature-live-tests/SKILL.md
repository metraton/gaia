---
name: signature-live-tests
description: Use when the user asks to run a live signature test or the whole catalog -- "ejecuta la prueba N", "corre el catálogo de firmas", "run test N" -- to certify Gaia's approvals in Claude Code or OpenCode
---

# Signature Live Tests

A fixed catalog that certifies, in the running host, that a signature shows
what Gaia sealed and runs only what the user approved. The catalog is the
same in Claude Code and OpenCode and is the evidence route for the live
certification of both hosts. You, the orchestrator, run it as a guided
certification: each test is one isolated specialist making one small,
reversible change to its own file; you tell the user what to do at each
signature and what to expect, and you check the result yourself.

Nothing is invented around it. There is no sandbox, no repository, no
fixture and no preparation beyond what each test names. Everything happens
in one fixed folder, `<scratch>/prueba-firmas/`, where `<scratch>` is the
`scratch=` line of `gaia paths`. Gaia's scratch retention never removes that
folder: it only reclaims entries named by a closed contract id, and
`prueba-firmas` is not one.

## Running a test

1. Say which test runs, what the user will be asked, and the answer to give
   ("in test 2 choose Reject").
2. Dispatch one `developer` specialist per file with the test's steps below,
   verbatim, and the absolute folder. Several tests may run at once: each owns
   its own file, so they never compete.
3. When it returns `APPROVAL_REQUEST`, present it with
   `orchestrator-present-approval` -- unchanged. Remind the user of the answer
   for this test right before the question opens.
4. Check with `gaia approvals show <approval_id>` and with the file listing the
   specialist records before cleaning. State and Outcome are separate lines:
   State is the user's decision (`pending`, `orphaned`, `approved`,
   `rejected`), Outcome is what became of an approved request (`executed`) and
   is absent while nothing ran. A test passes only when both lines and the
   files match its expected result; report which part did not.
5. The specialist removes its own files at the end (`rm` of the exact path,
   which inside scratch needs no signature). The folder and `otra/` stay,
   empty of test files.

The specialist's fixed steps: `mkdir -p <folder>` (and `<folder>/otra` for
test 5); create its file with the Write tool, which needs no signature; ask
the signature with `gaia approvals request-set` before running anything; run
the sealed command only after approval, byte for byte; record the folder
listing; remove its files.

## The catalog

Each command is a `mv` of the test's own file. Unless a test says otherwise
it is sealed with `--cwd` set to the specialist's own directory and absolute
paths, so the block shows no folder line.

**Test 1 -- Approve.** File `nota-1.txt`. Asks: rename it to `nota-1-ok.txt`.
User: Approve. Expected: State `approved`, Outcome `executed`; only
`nota-1-ok.txt` exists; nothing else in the folder changed.

**Test 2 -- Reject.** File `nota-2.txt`. Asks: rename it to `nota-2-ok.txt`.
User: Reject. Expected: State `rejected`, no Outcome line; `nota-2.txt`
unchanged. Resumed, the specialist runs the identical command once and it is
blocked again under a new id -- the rejected signature is not reusable;
withdraw that new one with `gaia approvals reject <new_id>`.

**Test 3 -- Details, then Approve.** File `nota-3.txt`. Asks: rename it to
`nota-3-ok.txt`. User: Details first. Expected: the Details block shows what
the command does, its impact, rollback, 30 min, ID and fingerprint; State is
still `pending`, no Outcome line, and `nota-3.txt` is unchanged. Ask again
with `gaia approvals question <approval_id>` (Details itself is
`gaia approvals question --details`); user: Approve. Expected: State
`approved`, Outcome `executed`; only `nota-3-ok.txt` exists.

**Test 4 -- Two commands, one signature.** File `nota-4.txt`. Asks one set:
1 rename `nota-4.txt` to `nota-4-a.txt`, 2 rename `nota-4-a.txt` to
`nota-4-b.txt`. User: one Approve. Expected: both run with no second
signature; State `approved`, Outcome `executed`; only `nota-4-b.txt` exists.

**Test 5 -- Another folder.** File `otra/nota-5.txt`. Asks: rename
`nota-5.txt` to `nota-5-ok.txt` with `--cwd <folder>/otra` and relative names.
User: Approve. Expected: the block shows `en <folder>/otra` with the command on
the next line; it runs as `cd <folder>/otra && <command>`; State `approved`,
Outcome `executed`; only `otra/nota-5-ok.txt` exists.

**Test 6 -- Several signatures in one question.** Two specialists, files
`nota-6a.txt` and `nota-6b.txt`, each asks to rename its file to `-ok`. Ask
both: in Claude Code one `gaia approvals question <id-6a> <id-6b>`; in
OpenCode, which asks one signature per call, two questions one after another.
User: Approve 6a, Reject 6b. Expected: each answer decides only its own
signature -- 6a State `approved`, Outcome `executed`, renamed; 6b State
`rejected`, no Outcome line, unchanged.

**Test 7 -- Typed answer.** File `nota-7.txt`. Asks: rename it to
`nota-7-ok.txt`. User: types an answer ("sí, aprobado") instead of choosing an
option. Expected: nothing activates; State `pending`, no Outcome line;
`nota-7.txt` unchanged. After 30 minutes with no activity from the requester
(likely in OpenCode, whose sessions do not heartbeat) the same request reads
State `orphaned`: accept it as the same result -- still undecided, no Outcome
line, file unchanged -- because only an Approve would read `approved`.
Withdraw it with `gaia approvals reject <approval_id>`.

**Test 8 -- How it looks.** File `nota-8.txt`. Asks: rename it to
`nota-8-ok.txt`. User: confirms the block reads `Solicitud de aprobación ·
<agent>`, a short description, a blank line, and the numbered command, exact,
on one line, with no validity; and that the question has at most 60
characters with Approve / Reject / Details. Then Details, and confirms that
block keeps what it does, impact, rollback, 30 min, ID and fingerprint without
the command. Then Reject. Expected: the user says both look right; State
`rejected`, no Outcome line; `nota-8.txt` unchanged.

Running the catalog is tests 1 to 8 in order, reporting per test: the answer
given, the State and Outcome from `gaia approvals show`, the file listing,
pass or fail.
