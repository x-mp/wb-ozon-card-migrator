# Recovery

1. Check out the exact reviewed commit and restore configuration through approved
   secret refs outside Git.
2. Set the canonical `MARKETPLACE_DATA_ROOT` and restore the matching immutable
   source runs and `operations/migrations` artifacts.
3. Install locked dependencies and run the full offline test suite.
4. Rebuild the migration plan from source refs and compare its digest with the
   retained plan. Never infer approval from a previous run.
5. Keep execution disabled until the owner explicitly confirms the new exact
   plan. Canary one product, verify the asynchronous Ozon result and save read-back.
6. Record commit, plan ID, source run IDs, result and limitations. Do not modify
   or delete the retained historical operation.

