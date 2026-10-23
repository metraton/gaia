"""The gaia CLI's argparse grammar by subcommand path: its subcommands and its declared option strings."""

SUBCOMMANDS = {
    '': (
        'ac approvals brief cleanup context contract defects dev doctor evidence history '
        'install lint login map memory metrics migrate milestone notifications now paths plan '
        'project query release scan session signals status task uninstall update usage '
        'workspace worktree'
    ),
    'ac': 'add edit remove set-status',
    'approvals': (
        'approve audit-secrets clean history list opencode-decide opencode-present pending '
        'question reject reject-all replay request-file-write request-set revoke show stats'
    ),
    'brief': (
        'ac close decision delete deps edit history list milestone new search set-project '
        'set-status show verify'
    ),
    'brief ac': 'add edit remove',
    'brief decision': 'add list',
    'brief milestone': 'add remove',
    'context': (
        'accept dump get get-contract move-contracts move-memory move-project project '
        'prune-workspaces query scan show wipe'
    ),
    'contract': (
        'add chain fill fill-rate finalize init list proposals reap reconcile set validate '
        'view'
    ),
    'contract proposals': 'accept backfill dismiss list',
    'evidence': 'add list orphans show',
    'lint': 'comments',
    'login': 'start status',
    'memory': (
        'add append checkpoint conflicts delete edit episode-show get-relevant link list '
        'reclassify search show stats story'
    ),
    'migrate': 'apply plan',
    'milestone': 'add edit remove set-status',
    'notifications': 'ack add cancel list show snooze',
    'paths': 'data db',
    'plan': 'change delete history list pause resume save set-status show',
    'plan change': 'apply approve list propose request',
    'project': 'move',
    'release': 'check publish',
    'session': 'preview resume-point snapshot',
    'session resume-point': 'set',
    'task': 'add cover depend edit gate list remove reorder set-status show',
    'task gate': 'add edit list remove reverify set-status',
    'usage': 'ingest show',
    'workspace': 'curate current declare info list merge retire',
    'worktree': 'create list prune-branches release show show-capture',
}

OPTIONS = {
    '': '--help --json --version -h',
    'ac': '--help --workspace -h',
    'ac add': (
        '--artifact-path --description --description-file --evidence-shape '
        '--evidence-shape-file --evidence-type --help --json --workspace -h'
    ),
    'ac edit': (
        '--artifact-path --description --description-file --evidence-shape '
        '--evidence-shape-file --evidence-type --help --json --workspace -h'
    ),
    'ac remove': '--help --json --workspace -h',
    'ac set-status': '--help --json --workspace -h',
    'approvals': '--help -h',
    'approvals approve': '--help --json --yes -h',
    'approvals audit-secrets': '--db --emit-sql --help --json -h',
    'approvals clean': '--dry-run --help --json -h',
    'approvals history': '--help --json --limit --status -h',
    'approvals list': '--all-sessions --help --include-stale --json --orphans-only --session -h',
    'approvals opencode-decide': '--call-id --decision-lane --help --json --reply --session-id --token -h',
    'approvals opencode-present': (
        '--agent-id --call-id --help --json --presenter-session-id --preview --session-id '
        '--signature --token -h'
    ),
    'approvals pending': '--all-sessions --help --include-stale --json --session -h',
    'approvals question': '--details --help -h',
    'approvals reject': '--all --help --json --reason -h',
    'approvals reject-all': '--dry-run --help --workspace -h',
    'approvals replay': '--dry-run --help --json --yes -h',
    'approvals request-file-write': (
        '--agent-id --does --help --impact --json --path --question --rationale --rollback '
        '--session-id --verification --what -h'
    ),
    'approvals request-set': (
        '--agent-id --command --cwd --does --expect-exit --help --impact --json --question '
        '--rationale --rollback --session-id --shared-state --verification --what -h'
    ),
    'approvals revoke': '--help --reason --yes -h',
    'approvals show': '--consent-surface --help --json -h',
    'approvals stats': '--help --json -h',
    'brief': '--help --workspace -h',
    'brief ac': '--help -h',
    'brief ac add': (
        '--artifact --description --description-file --evidence-shape --evidence-shape-file '
        '--evidence-type --help --id --json --workspace -h'
    ),
    'brief ac edit': (
        '--artifact --description --description-file --evidence-shape --evidence-shape-file '
        '--evidence-type --help --id --json --workspace -h'
    ),
    'brief ac remove': '--help --id --json --workspace -h',
    'brief close': '--help --workspace -h',
    'brief decision': '--help -h',
    'brief decision add': '--help --json --rationale --supersedes --text --workspace -h',
    'brief decision list': '--help --json --workspace -h',
    'brief delete': '--help --json --workspace --yes -h',
    'brief deps': '--help --json --workspace -h',
    'brief edit': '--append --content --content-file --field --headless --help --json --workspace -h',
    'brief history': '--help --json --workspace -h',
    'brief list': '--format --help --json --status --workspace -h',
    'brief milestone': '--help -h',
    'brief milestone add': '--description --help --json --name --order --workspace -h',
    'brief milestone remove': '--help --json --name --workspace -h',
    'brief new': (
        '--approach --approach-file --context --context-file --headless --help --json '
        '--objective --objective-file --out-of-scope --out-of-scope-file --project --status '
        '--surface-type --title --workspace -h'
    ),
    'brief search': '--help --json --limit --workspace -h',
    'brief set-project': '--clear --dry-run --force --help --json --workspace -h',
    'brief set-status': '--help --json --workspace -h',
    'brief show': '--help --json --workspace -h',
    'brief verify': '--help --json --workspace -h',
    'cleanup': '--dry-run --help --json --prune --retain -h',
    'context': '--help -h',
    'context accept': '--dry-run --help --json --workspace -h',
    'context dump': '--help --include-missing --json --section --text --workspace -h',
    'context get': '--help --include-missing --json --section --text --workspace -h',
    'context get-contract': '--help --json --project --section --text --workspace -h',
    'context move-contracts': '--contract --dry-run --from --help --json --on-conflict --to --yes -h',
    'context move-memory': '--dry-run --from --help --json --name --on-conflict --to --yes -h',
    'context move-project': (
        '--decision --dry-run --from-name --from-workspace --help --json --to-name '
        '--to-workspace --yes -h'
    ),
    'context project': '--help --json --workspace -h',
    'context prune-workspaces': '--dry-run --help --json --yes -h',
    'context query': '--help -h',
    'context scan': '--dry-run --help --json -h',
    'context show': '--help --json --section -h',
    'context wipe': '--help --purge-memory --workspace --yes -h',
    'contract': '--help -h',
    'contract add': '--agent-id --draft-id --help --json -h',
    'contract chain': '--contract-id --help --json -h',
    'contract fill': '--agent-id --draft-id --help --json --json-file -h',
    'contract fill-rate': '--help --json --min --since --until --workspace -h',
    'contract finalize': '--agent-id --draft-id --help --json --plan-task-id --session-id --workspace -h',
    'contract init': '--agent-id --draft-id --help --json -h',
    'contract list': (
        '--agent --agent-id --agent-name --brief --contract-id --cut --grep --harness-id '
        '--help --json --limit --plan-task --session --since --state --task --until '
        '--workspace -h'
    ),
    'contract proposals': '--help -h',
    'contract proposals accept': '--help --json --project --workspace -h',
    'contract proposals backfill': '--apply --help --json --since-days -h',
    'contract proposals dismiss': '--help --json --reason -h',
    'contract proposals list': '--all --contract-id --help --json --kind -h',
    'contract reap': '--help --json --older-than-seconds -h',
    'contract reconcile': '--contract-id --harness-id --help --json --superseded-by -h',
    'contract set': '--agent-id --draft-id --help --json -h',
    'contract validate': '--agent-id --draft-id --help --json -h',
    'contract view': '--agent-id --contract-id --draft-id --field --harness-id --help --json --row -h',
    'defects': (
        '--agent --count --help --json --limit --origin --severity --since --type --until '
        '--workspace -h'
    ),
    'dev': (
        '--channel --from-worktree --help --host --keep-tarball --no-global-link --pack-dest '
        '--quiet --ref --verbose --workspace -h -v'
    ),
    'doctor': '--fix --help --json --workspace -h',
    'evidence': '--help -h',
    'evidence add': (
        '--ac --artifact-file --brief --created-by --gate --help --json --negative --task '
        '--text --type --workspace -h'
    ),
    'evidence list': '--ac --brief --help --json --workspace -h',
    'evidence orphans': '--help --json -h',
    'evidence show': '--help --json -h',
    'history': '--agent --blocked --help --json --limit --today --workspace -a -b -h -n -t',
    'install': (
        '--channel --db-path --help --host --no-path --path --postinstall --quiet '
        '--skip-workspace --strict-wiring --verbose --workspace -h -v'
    ),
    'lint': '--help -h',
    'lint comments': '--base --head --help --json --repo -h',
    'login': '--help -h',
    'login start': '--account --force --help -h',
    'login status': '--help -h',
    'map': '--help -h',
    'memory': '--help --json -h',
    'memory add': (
        '--audience --body --body-file --class --description --help --initiative --json '
        '--measured-at --method --name --project --project-ref --replace --status --type '
        '--workspace -h'
    ),
    'memory append': '--body --body-file --help --json --workspace -h',
    'memory checkpoint': '--file --help --initiative --json --project --project-ref --workspace -h',
    'memory conflicts': '--help --json --threshold -h',
    'memory delete': '--hard --help --json --workspace --yes -h',
    'memory edit': "",
    'memory episode-show': '--help --json -h',
    'memory get-relevant': (
        '--help --initiative --json --limit --max-chars --no-pointer --project --sections '
        '--types --workspace -h'
    ),
    'memory link': '--delete --help --json --kind --workspace -h',
    'memory list': (
        '--audience --class --format --help --json --limit --order --sort --status --type '
        '--workspace -h'
    ),
    'memory reclassify': '--class --help --json --status --workspace -h',
    'memory search': '--help --json --limit --scope --workspace -h',
    'memory show': '--help --history --json --links --workspace -h',
    'memory stats': '--help --json -h',
    'memory story': '--help --json --max-depth --workspace -h',
    'metrics': '--agent --help --json --range --since --until --workspace -h',
    'migrate': '--help -h',
    'migrate apply': '--consent-chain --db --help -h',
    'migrate plan': '--db --help -h',
    'milestone': '--help --workspace -h',
    'milestone add': '--description --help --json --order --workspace -h',
    'milestone edit': '--description --help --json --new-name --workspace -h',
    'milestone remove': '--help --json --workspace -h',
    'milestone set-status': '--help --json --workspace -h',
    'notifications': '--help -h',
    'notifications ack': '--all --all-workspaces --help --json --workspace -h',
    'notifications add': (
        '--at --body --cron --every --headline --help --json --kind --memory --project '
        '--session-id --skill --task --workspace -h'
    ),
    'notifications cancel': '--help --json -h',
    'notifications list': '--all-workspaces --help --json --limit --unread --upcoming --workspace -h',
    'notifications show': '--help --json -h',
    'notifications snooze': '--for --help --json --until -h',
    'now': '--help --json -h',
    'paths': '--help -h',
    'paths data': '--help -h',
    'paths db': '--help -h',
    'plan': '--help --workspace -h',
    'plan change': '--help -h',
    'plan change apply': '--content-file --help --json --workspace -h',
    'plan change approve': '--help --json --workspace -h',
    'plan change list': '--help --json --workspace -h',
    'plan change propose': '--affects --help --json --summary --workspace -h',
    'plan change request': '--help --json --reason --workspace -h',
    'plan delete': '--help --json --workspace --yes -h',
    'plan history': '--content --help --json --workspace -h',
    'plan list': '--brief --format --help --json --status --workspace -h',
    'plan pause': '--help --json --reason --workspace -h',
    'plan resume': '--help --json --workspace -h',
    'plan save': '--brief --content --content-file --help --json --reason --status --workspace -h',
    'plan set-status': '--help --json --workspace -h',
    'plan show': '--help --json --workspace -h',
    'project': '--help -h',
    'project move': '--dry-run --from --help --into --json -h',
    'query': (
        '--agent --command-like --count --failed --format --group-by --help --json --last '
        '--metrics --since --snippets --surface --type --until --workspace -h'
    ),
    'release': '--help -h',
    'release check': '--functional --gh --help --local-suite --quiet -h',
    'release publish': '--branch --dry-run --gh --help --local-suite --quiet -h',
    'scan': '--dry-run --help --json --workspace -h',
    'session': '--help -h',
    'session preview': '--help --session-id -h',
    'session resume-point': '--help -h',
    'session resume-point set': '--help --session-id --text -h',
    'session snapshot': '--help --json --session-id -h',
    'signals': '--help --json --limit --since -h',
    'status': '--help --json -h',
    'task': '--help --workspace -h',
    'task add': '--goal --help --json --order --workspace -h',
    'task cover': '--help --json --remove --workspace -h',
    'task depend': '--help --json --remove --workspace -h',
    'task edit': '--goal --goal-file --help --json --workspace -h',
    'task gate': '--help -h',
    'task gate add': '--artifact-path --evidence-shape --evidence-type --help --json --type --workspace -h',
    'task gate edit': (
        '--artifact-path --evidence-shape --evidence-shape-file --evidence-type --help --json '
        '--verification-type --workspace -h'
    ),
    'task gate list': '--help --json --workspace -h',
    'task gate remove': '--help --json --workspace -h',
    'task gate reverify': '--help --json --reason --workspace -h',
    'task gate set-status': '--cause --help --json --workspace -h',
    'task list': '--format --help --json --plan --status --workspace -h',
    'task remove': '--help --json --workspace -h',
    'task reorder': '--from --help --json --to --workspace -h',
    'task set-status': '--help --json --override --reason --workspace -h',
    'task show': '--help --id --json --workspace -h',
    'uninstall': (
        '--channel --db-path --dry-run --help --json --no-backup --preuninstall --quiet '
        '--snapshot-dir --workspace -h'
    ),
    'update': (
        '--channel --db-path --dry-run --help --host --json --no-path --path --postinstall '
        '--quiet --skip-workspace --strict-wiring --verbose --workspace -h -v'
    ),
    'usage': '--help -h',
    'usage ingest': '--help --json --projects-dir --session -h',
    'usage show': '--help --json --plan --session --since --until -h',
    'workspace': '--help -h',
    'workspace curate': '--dry-run --help --into --json --on-conflict --yes -h',
    'workspace current': '--help -h',
    'workspace declare': '--dry-run --help -h',
    'workspace info': '--help -h',
    'workspace list': '--help -h',
    'workspace merge': '--confirm --dry-run --help --report-duplicates -h',
    'workspace retire': '--dry-run --help --into --json --on-conflict --undo --yes -h',
    'worktree': '--help -h',
    'worktree create': '--agent-id --base --branch --contract-id --help --json --project --repo -h',
    'worktree list': '--help --json --repo -h',
    'worktree prune-branches': '--dry-run --help --json --keep-branch --repo -h',
    'worktree release': (
        '--ac --brief --contract-id --created-by --help --json --keep-branch --repo --task-id '
        '--workspace -h'
    ),
    'worktree show': '--help --json -h',
    'worktree show-capture': '--diff --help --json -h',
}
