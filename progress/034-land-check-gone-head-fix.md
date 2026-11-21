11:08 steer s1 applied: rerunning step 1's gate in the foreground until it records, then steps 2 to 4 in the same turn
11:08 step 1 c3bce56db: an ordinary send carries only its run's evidence and does not fetch origin
11:20 step 2 6598293c7: an ordinary message with a missing evidence commit syncs the store and names the ref; hand-overs stay strict
11:35 step 3 868a00931: land-check blames and credits only what could cover the unit
11:46 step 4 70ed949c2: a failed pin warns; gates-mode verification pins its range; dead NoBrief skip dropped
