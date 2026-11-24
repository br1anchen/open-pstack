22:52 step 1 committed: nest.instructions printed after a start records a pending row
23:03 step 1 b6ff274ea: a start prints the nest instructions of the thread it started
23:16 step 2 committed: pending nest row keeps the thread; archived pending thread gets three calls; sweep tests mark nest done first
23:26 step 2 b48455eb4: a pending nest row keeps a thread from the sweep; archived pending threads get the three-call instruction
23:29 step 3 committed: take_anchor nests the whole run; the old master now stays until nested (tests updated)
23:39 step 3 1b8c37d1e: take-master nests every thread of the run under the new anchor
23:40 step 4: amp export and get_thread_status carry no parent field, so no code change; step 5 docs committed
23:50 step 4 6b2551f9b: docs: nest instructions on starts, pending rows keep threads, whole-run re-parenting, --done unchecked
