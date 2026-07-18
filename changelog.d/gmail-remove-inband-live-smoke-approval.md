### Removed

- `acquire_gmail_sent`: the acquisition-time in-band `--live-smoke-confirmed`
  approval path is removed. Passing it is now hard-refused (exit 2), directing to
  the separated `smoke` → `validate-smoke` → `approve-smoke` flow, so an approval
  receipt is never minted while acquiring or writing records. Approval only
  validates an already-closed smoke tree, prompts, and mints without acquiring or
  changing any records.
