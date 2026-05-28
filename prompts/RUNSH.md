### Running your implementation

Your deliverable is a single executable file `./run.sh` in the workspace root, plus whatever source files it invokes. When invoked:

```sh
cd /sandbox && ./run.sh
```

it must exit 0 and write the output CSV(s) described above.

**`./run.sh` is the unit of work being measured** — its wall-clock time is the headline cost metric for the experiment. It must do the **entire** workload: any preprocessing the chosen framework needs (e.g. Josh's `.jshd` build, or netCDF→DataFrame conversion for Mesa), the simulation, and emission of the output CSV(s). No "pre-step" the user is expected to run by hand. The contract is "one script, end-to-end."

We've seeded `/sandbox/run.sh` for you — it's already executable and runs 2 replicates by default. **Fill in the body** with your preprocess + simulation invocation, using `"$N_REPLICATES"` for the replicate count. Two replicates is the self-test scale.

You may invoke `./run.sh` freely to self-test. Before you declare yourself done, execute it once and confirm it exits 0 and writes the output CSV(s) at the expected path. If it fails, fix the cause and re-run.
