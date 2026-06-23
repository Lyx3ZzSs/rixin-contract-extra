# Extraction eval samples

Each sample is a pair of files sharing a base name:

- `<name>.pdf` (or `.png`/`.jpg`) — the raw contract document.
- `<name>.golden.json` — expected field values:

```json
{
  "party-a-name": "北京日新科技有限公司",
  "party-b-name": "上海恒信信息技术有限公司",
  "contract-amount": "1,200,000.00"
}
```

## Running

```
# baseline (mock providers — plumbing only):
pytest -m eval

# real provider comparison (configure .env first):
OCR_PROVIDER=ppocr      pytest -m eval     # record baseline F1
OCR_PROVIDER=ppstructurev3 pytest -m eval  # record upgraded F1
```

Compare the two F1 reports — especially on table-heavy fields
(`contract-amount`, `prepayment-ratio`, payment-schedule items) — to validate
the PP-StructureV3 upgrade (spec acceptance criterion §7.1).

Place real desensitized samples here. Until then the harness runs on the mock
provider only.
