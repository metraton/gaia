# Technical Explanation -- examples

Three worked explanations in the DevOps register, each showing the four
decisions of `SKILL.md` Step 1 being taken and the fixed order of Step 2 being
followed, then every anti-pattern shown before and after. Every fenced picture
here stays within 80 columns (Step 4); where a level-2 picture would not fit in
one row it is drawn top-down instead. The component names
below are illustrative: they name the shape of a typical setup, not a file in
this repository.

## Example 1 -- a Kubernetes workload, control and traffic separated

**The four decisions.** Intent: the reader should be able to tell, when the
workload misbehaves, whether to look at what CONTROLS it or at what REACHES it.
Mode: concept. Representation: the idea moves (a change travels in; a request
travels through) and diverges (two independent paths) -- two flows, so two
drawings. Density: normal.

**Level 1 -- what exists, how it connects.** Five boxes, common nouns.

```
control flow (who tells whom what to run)

  the repository ──declares desired state──► the reconciler
                                                   │
                                          compares and applies
                                                   ▼
                                             the cluster
                                                   │
                                            runs the copies
                                                   ▼
                                         the workload's copies
```

```
traffic flow (what a request passes through)

  the user ──► the edge ──► the router ──► the workload's copies
              (public IP)  (path rules)   (any healthy one)
```

**What happens.** A change is merged into the repository; the reconciler
notices, compares what is declared with what is running, and moves the cluster
toward the declaration. Independently, a request arrives at the edge, is routed
by its path to the workload, and lands on any copy that is healthy.

**Why two drawings.** The two flows share the copies and nothing else. When
the copies are wrong, the control flow is where to look; when the copies are
right and users still fail, the traffic flow is. A reader holding one picture
with both would have to untangle which arrow is "tells" and which is "sends".

**Level 2 -- the real components.** The same two shapes, now named.

```
control flow

  Git repository (deploy/ overlays)
        │
        │ declares HelmRelease + Kustomization
        ▼
  Flux controllers
        │
        │ apply to the API server
        ▼
  Deployment ──creates──► ReplicaSet ──creates──► Pods
```

```
traffic flow

  client ──► cloud load balancer ──► Ingress (nginx) ──► Service ──► Pods
                                     host + path rule    ready Pods only
```

**Detail** (reached on request): the Flux `Kustomization` interval and the
`HelmRelease` values; the Service's readiness gate, which is why a Pod that is
running but not ready receives no traffic; the Ingress annotation that sets the
timeout.

**Plain register, closing sentence.** The cluster is running what the
repository declares; if users still fail, the fault is on the way in, not in
what is running.

## Example 2 -- a CI/CD pipeline at level 1 and level 2

**The four decisions.** Intent: the reader should know where a change is at any
moment and what gate it has to clear next. Mode: concept (what the pipeline is),
with a procedure beneath it (how to push a change through). Representation: the
idea moves and diverges (a path through stages) -- a flow, phases in reading
order. Density: normal.

**Level 1.** Five stages, common nouns, no tool names.

```
  a change ──► build ──► verify ──► publish ──► roll out
              (make it)  (prove it) (store it)  (run it)
```

What happens: a change enters on the left and either leaves on the right or
stops at the first stage that rejects it; nothing is published that was not
verified, and nothing rolls out that was not published.

**Level 2.** The same five stages, real components on the boxes level 1 placed.

```
  pull request ──► GitHub Actions: build job ──► test job
                   (container image)             (unit + lint)
                                                      │
                                                      ▼
                   push to the registry ──► Flux picks the new tag
                   (image:sha tag)          (HelmRelease values bump)
```

Where a change waits: the PR until review; the test job until green; the
registry until the tag is referenced; the cluster until the reconciler's next
interval. Detail on request: the workflow file's job names, the registry path,
the reconciliation interval, the image-policy that promotes the tag.

**Ours vs inherited.** The build and test jobs are ours; the registry and the
reconciler are shared with the platform team and change on their schedule.

**Closing sentence.** A change that is green in verify and visible in the
registry will run within one reconciliation interval; anything slower is the
platform side, not the pipeline.

## Example 3 -- a troubleshooting flow at urgent density

**The four decisions.** Intent: the on-call reader acts in the next minute.
Mode: procedure. Representation: the idea moves and converges (paths collapse
into one action) -- a decision flow. Density: brief -- STATE -> PROBLEM ->
ACTION, nothing before the state.

**STATE.** Checkout is returning errors to about a third of users; the
workload's copies are all running and reporting ready.

**PROBLEM.** The copies are healthy, so the fault is on the way in: one of the
three edge nodes is routing to a copy that no longer exists, because its routing
table did not refresh after last night's rollout.

**ACTION.** Drain the stale edge node so the other two take its share; then
force the routing refresh. The rollout itself is fine and is not rolled back.

```
  errors on checkout?
    │
    ├── copies not ready ──► control-flow fault: look at the reconciler
    │
    └── copies ready ──► traffic-flow fault
                           │
                           ├── all edges stale ──► refresh routing everywhere
                           │
                           └── one edge stale ──► drain that edge, then refresh
```

Nothing below this line is read during the incident: the technical detail (the
edge's endpoint-slice cache, the refresh command, the rollout's diff) lives in
the follow-up and is reached after the action is taken.

## Anti-patterns, before and after

**Fifteen concepts before a map.**
Before: "The system has an ingress, a service mesh, two node pools, a Flux
source, a Kustomization, a HelmRelease, an image-update automation, a registry,
a Cloud SQL proxy, a secrets operator, an external-dns controller, a cert
manager, a horizontal autoscaler, a pod disruption budget and a network policy."
After: the level-1 picture of Example 1 -- five boxes -- and then the fifteen
names arrive at level 2 and in detail, each on a box the reader already holds.

**Identifiers in level 1.**
Before: "The `ci-deployer@proj-4821.iam.gserviceaccount.com` account applies
manifests to namespace `pay-svc-prod-eu1`."
After: "The CI machine applies the declared state to the payments cluster." The
account name and the namespace are level-2 facts, on the boxes "CI machine" and
"payments cluster".

**A reference dump answering a "what is X" question.**
Before, asked "what is a HelmRelease": a table of every field of the
HelmRelease spec.
After: "A HelmRelease is the declaration Flux reads to install and keep a Helm
chart at a chosen version with chosen values; when the declaration changes, Flux
changes the installation to match." The field table is the reference mode, and
it is linked for the reader who arrives to look one field up.

**Invented notation.**
Before: `[svc] =*=> {pods}  (=*=> means "load-balanced, sticky")`.
After: `Service ──sends each request to a ready Pod──► Pods`, and "sticky
sessions are on" as a sentence beneath it.

**Decoration.**
Before: a paragraph saying "the build job runs before the test job", followed
by a two-box diagram saying the same.
After: the sentence alone. Two parts and one relation is a sentence.

**One picture carrying two flows.**
Before: one drawing where the repository, the reconciler, the load balancer,
the router and the Pods all point at each other with unlabelled arrows.
After: the two drawings of Example 1, one for control and one for traffic.

**Vagueness sold as plainness.**
Before: "Some resources would have been affected by the plan."
After, plain: "The plan would have deleted the production database, which is
serving users right now." After, technical beneath it: "`terraform plan` shows
`google_sql_database_instance.prod` as destroy-and-recreate because the
`region` attribute changed."
