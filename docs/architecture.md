# Architecture

```text
WB official API          Ozon read API            Local preparation
       |                       |                          |
       +---- immutable raw ----+---- dictionaries -------+
                                                          |
                                                   canonical card
                                                          |
                                                   validation plan
                                                          |
                                               explicit approval file
                                                          |
                                               Ozon writer (disabled)
                                                          |
                                                async task status check
```

WB is read-only. Ozon discovery and Ozon upload are separate commands. Merely
adding an API key never enables a write: the uploader also requires a valid
plan, an approval tied to the plan hash, an exact confirmation string, and the
temporary `OZON_ENABLE_WRITES` guard.

Every marketplace response is stored under a new run directory with request
time, source, entity, safe parameters, HTTP status, content type, checksum, and
documentation URL. Existing raw responses are never overwritten.

The first production run is limited to one card. Price/stock management,
deletion, archiving, and bulk uploads are intentionally outside the first
pilot.
